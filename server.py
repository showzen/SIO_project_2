import asyncio
import json
import base64
import argparse
import coloredlogs, logging
import re
import os
from aio_tcpserver import tcp_server
import security
from timeit import default_timer as timer
import datetime as datetime
import pickle
logger = logging.getLogger('root')

STATE_CONNECT = 0
STATE_OPEN = 1
STATE_DATA = 2
STATE_CLOSE= 3
STATE_NEGOTIATION = 4
STATE_EXCHANGE = 5
time = datetime.datetime.now()

#GLOBAL
storage_dir = 'files'

class ClientHandler(asyncio.Protocol):
	def __init__(self, signal):
		"""
		Default constructor
		"""
		self.signal = signal
		self.state = 0
		self.file = None
		self.file_name = None
		self.file_path = None
		self.storage_dir = storage_dir
		self.buffer = ''
		self.peername = ''
		self.keys = []
		self.fernet_key = security.gen_Fernet_key()
		self.fernet_filename = "fernet_key"
		security.store_Fernet_key(self.fernet_key,self.fernet_filename)

	def connection_made(self, transport) -> None:
		"""
		Called when a client connects

		:param transport: The transport stream to use with this client
		:return:
		"""
		self.peername = transport.get_extra_info('peername')
		logger.info('\n\nConnection from {}'.format(self.peername))
		self.transport = transport
		self.state = STATE_CONNECT


	def data_received(self, data: bytes) -> None:
		"""
        Called when data is received from the client.
        Stores the data in the buffer

        :param data: The data that was received. This may not be a complete JSON message
        :return:
        """
        
		logger.debug('Received: {}'.format(data))

		data = security.fernet_decript(self.fernet_key,data)

		try:
			self.buffer += data.decode()
		except:
			logger.exception('Could not decode data from client')

		idx = self.buffer.find('\r\n')

		while idx >= 0:  # While there are separators
			frame = self.buffer[:idx + 2].strip()  # Extract the JSON object
			self.buffer = self.buffer[idx + 2:]  # Removes the JSON object from the buffer

			self.on_frame(frame)  # Process the frame
			idx = self.buffer.find('\r\n')

		if len(self.buffer) > 4096 * 1024 * 1024:  # If buffer is larger than 4M
			logger.warning('Buffer to large')
			self.buffer = ''
			self.transport.close()


	def on_frame(self, frame: str) -> None:
		"""
		Called when a frame (JSON Object) is extracted

		:param frame: The JSON object to process
		:return:
		"""
		#logger.debug("Frame: {}".format(frame))

		try:
			message = json.loads(frame)
		except:
			logger.exception("Could not decode JSON message: {}".format(frame))
			self.transport.close()
			return

		mtype = message.get('type', "").upper()
		print(mtype)

		if mtype == 'OPEN':
			ret = self.process_open(message)
		elif mtype == 'RSA_EXCHANGE':
			ret = self.process_RSA_exchange(message)
		elif mtype == 'DH_EXCHANGE':
			ret = self.process_DH_exchange(message)
		elif mtype == 'DATA':
			ret = self.process_data(message)
		elif mtype == 'CLOSE':
			ret = self.process_close(message)
		elif mtype == 'NEGOTIATION':
			ret = self.process_negotiation(message)
			
		else:
			logger.warning("Invalid message type: {}".format(message['type']))
			ret = False

		print (mtype,ret)
		if not ret:
			try:
				self._send({'type': 'ERROR', 'message': 'See server'})
			except:
				pass # Silently ignore

			logger.info("Closing transport")
			if self.file is not None:
				self.file.close()
				self.file = None

			self.state = STATE_CLOSE
			self.transport.close()
	

	def process_negotiation(self, message: str) -> bool:
		"""
		Processes an NEGOTIATION message from the client
		This message should contain the cript object that contains the algorithms 
		suggested for the encription  to 
		ensure a safe comunication during this session.

		:param message: The message to process
		:return: Boolean indicating the success of the operation
		"""
			
		self.cript = json.loads(message['cript'])
		if not self.validate_negotiation(self.cript) :
			return False

		message = {'type': 'OK'}

		self._send(message)

		self.state = STATE_EXCHANGE
		
		return True
		
	def validate_negotiation(self,cript_object):
		return True
		
	def process_RSA_exchange(self, message: str) -> bool:
		
		#parameters = message['parameters']		
		
		client_rsa_public_key = message['client_rsa_public_key']
		
		self.client_rsa_public_key = security.deserializePublicKey(client_rsa_public_key)
						
		self.rsa_private_key,self.rsa_public_key = security.get_rsa_asymn_keys()
		
		self.add_key(self.rsa_private_key)
		
		self.add_key(self.rsa_public_key)
		
		rsa_public_key = security.serializePublicKey(self.rsa_public_key).decode("utf8")
		
		self.iv,self.sym_key,self.encryptor = security.encryptor()
		
		iv_enc = security.encrypt(self.client_rsa_public_key,self.iv)[0].decode("iso-8859-1")
		
		sym_key_enc = security.encrypt(self.client_rsa_public_key,self.sym_key)[0].decode("iso-8859-1")
		
		message = {'type': 'RSA_EXCHANGE','server_rsa_public_key':rsa_public_key,'iv_enc':iv_enc,'sym_key_enc':sym_key_enc}
		
		self._send(message)
		
		return True
		
	def add_key(self,key):
		self.keys.append(key)
		
	
	def process_DH_exchange(self, message: str) -> bool:
		
		client_dh_public_key = message['client_dh_public_key']
		
		parameters = message['enc_parameters'].encode("iso-8859-1")
		
		iv,key,self.decryptor = security.decryptor(iv=self.iv,key=self.sym_key)
		
		#parameters = security.decrypt(self.decryptor,parameters)[0]
		
		self.parameters = security.deserializeParameters(parameters)
		
		self.client_dh_public_key = security.deserializePublicKey(client_dh_public_key)
		
		self.add_key(self.client_dh_public_key)
		
		self.dh_private_key,self.dh_public_key = security.get_asymm_keys(self.parameters)
		
		self.add_key(self.dh_private_key)
		
		self.add_key(self.dh_public_key)
		
		dh_public_key = security.serializePublicKey(self.dh_public_key).decode("utf8")

		shared_key = security.shared_key(self.dh_private_key,self.client_dh_public_key)
		
		self.shared_key = security.derive_key(shared_key,self.cript['digest'])
				
		message = {'type': 'DH_EXCHANGE','server_dh_public_key':dh_public_key}
		
		self._send(message)
		
		self.state = STATE_DATA
		return True


		
	def process_open(self, message: str) -> bool:
		"""
		Processes an OPEN message from the client
		This message should contain the filename

		:param message: The message to process
		:return: Boolean indicating the success of the operation
		"""
		logger.debug("Process Open: {}".format(message))
		
		
		if self.state != STATE_CONNECT:
			logger.warning("Invalid state. Discarding")
			return False

		if not 'file_name' in message:
			logger.warning("No filename in Open")
			return False

		# Only chars and letters in the filename
		file_name = re.sub(r'[^\w\.]', '', message['file_name'])
		file_path = os.path.join(self.storage_dir, file_name)

		
		if not os.path.exists("files"):
			try:
				os.mkdir("files")
			except:
				logger.exception("Unable to create storage directory")
				return False

		try:
			self.file = open(file_path, "wb")
			logger.info("File open")
		except Exception:
			logger.exception("Unable to open file")
			return False



		message = {'type': 'OK'}
		

		
		self._send(message)
		



		self.file_name = file_name
		self.file_path = file_path
		self.state = STATE_OPEN
		return True


	def process_data(self, message: str) -> bool:
		"""
		Processes a DATA message from the client
		This message should contain a chunk of the file

		:param message: The message to process
		:return: Boolean indicating the success of the operation
		"""
		logger.debug("Process Data: {}".format(message))
		
		if self.state == STATE_OPEN:
			self.state = STATE_DATA
			# First Packet

		elif self.state == STATE_DATA:
			data = security.decrypt_message(message,self.sym_key,self.rsa_private_key)
			message = data.get('message',None).replace("'","\"")
			message = json.loads(message)
			data = message.get('data', None)

		else:
			logger.warning("Invalid state. Discarding")
			return False

		try:

			if data is None:
				logger.debug("Invalid message. No data found")
				return False
			iv =  base64.b64decode(message['iv'])
			bdata = base64.b64decode(message['data'])
			#get decryptor to decrypt the encrypted data
			iv,key,decryptor = security.decryptor(key = self.shared_key,iv=iv)
			bdata = security.decrypt(self.decryptor,bdata,hashing=self.cript['digest'])
			bdata = security.decrypt(decryptor,bdata,hashing=self.cript['digest'])

		except:
			logger.exception("Could not decode base64 content from message.data")
			return False

		try:
			self.file.write(bdata)
			self.file.flush()
		except:
			logger.exception("Could not write to file")
			return False

		return True


	def process_close(self, message: str) -> bool:
		"""
		Processes a CLOSE message from the client.
		This message will trigger the termination of this session

		:param message: The message to process
		:return: Boolean indicating the success of the operation
		"""
		logger.debug("Process Close: {}".format(message))

		self.transport.close()
		if self.file is not None:
			self.file.close()
			self.file = None

		self.state = STATE_CLOSE
		
		time_diff = (datetime.datetime.now() - time).seconds
		if time_diff> 300 :
			self.keys = []
		return True
	

		
		
	def _send(self, message: str) -> None:
		"""
		Effectively encodes and sends a message
		:param message:
		:return:
		"""
		message_b = (json.dumps(message) + '\r\n').encode()
		message_b = security.fernet_encript(self.fernet_key,message_b)
		
		logger.debug("Send: {}".format(message_b))

		self.transport.write(message_b)

def main():
	global storage_dir

	parser = argparse.ArgumentParser(description='Receives files from clients.')
	parser.add_argument('-v', action='count', dest='verbose',
						help='Shows debug messages (default=False)',
						default=0)
	parser.add_argument('-p', type=int, nargs=1,
						dest='port', default=5000,
						help='TCP Port to use (default=5000)')

	parser.add_argument('-d', type=str, required=False, dest='storage_dir',
						default='files',
						help='Where to store files (default=./files)')

	args = parser.parse_args()
	storage_dir = os.path.abspath(args.storage_dir)
	level = logging.DEBUG if args.verbose > 0 else logging.INFO
	port = args.port
	if port <= 0 or port > 65535:
		logger.error("Invalid port")
		return

	if port < 1024 and not os.geteuid() == 0:
		logger.error("Ports below 1024 require eUID=0 (root)")
		return

	coloredlogs.install(level)
	logger.setLevel(level)

	logger.info("Port: {} LogLevel: {} Storage: {}".format(port, level, storage_dir))
	tcp_server(ClientHandler, worker=2, port=port, reuse_port=True)


if __name__ == '__main__':
	main()


