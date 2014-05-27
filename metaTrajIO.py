"""
	Read binary ionic current data into numpy arrays

	Author: Arvind Balijepalli
	Created:	7/17/2012

	ChangeLog:
		5/27/14		AB 	Added dcOffset kwarg to initialization to allow 
						for offset correction in the ionic current data.
		2/13/14		AB 	Fixed a potential infinite recursion bug in the
						initialization. 
		7/17/12		AB	Initial version
"""
from abc import ABCMeta, abstractmethod
import glob
import numpy as np

import settings

# define custom exceptions
class IncompatibleArgumentsError(Exception):
	pass
class InsufficientArgumentsError(Exception):
	pass
class IncorrectDataFormat(Exception):
	pass
class EndOfFileError(Exception):
	pass
class SamplingRateChangedError(Exception):
	pass
class EmptyDataPipeError(Exception):
	pass
class FileNotFoundError(Exception):
	pass

class metaTrajIO(object):
	__metaclass__=ABCMeta

	def __init__(self, **kwargs):
		"""
			Initialize a TrajIO object. The object can load all the data in a directory,
			N files from a directory or from an explicit list of filenames. In addition 
			to the arguments defined below, implementations of this meta class may require 
			the definition of additional arguments. See the documentation of those classes
			for what those may be. For example, the qdfTrajIO implementation of metaTrajIO also requires
			the feedback resistance (Rfb) and feedback capacitance (Cfb) to be passed at initialization.

			Args: The arguments	passed to init change based on the method of file IO selected:
				dirname		all files from a directory ('<full path to data directory>')
				nfiles		if requesting N files (in addition to dirname) from a specified directory
				
				fnames 		explicit list of filenames ([file1, file2,...]). This argument 
							cannot be used in conjuction with dirname/nfiles. The filter 
							argument is ignored when used in combination with fnames. 

				filter		'<wildcard filter>' (optional, filter is '*'' if not specified)
				start 		Data start point. This allows the first 'n' specified to be skipped
							and excluded from any data analysis
				datafilter	Handle to the algorithm to use to filter the data. If no algorithm is specified, datafilter
							is None and no filtering is performed.
				dcOffset	Subtract a DC offset from the ionic current data.
			Returns:
				None
			Properties:
				FsHz		sampling frequency in Hz. If the data was decimated, this property will hold the
							sampling frequency after decimation.
			Errors:
				IncompatibleArgumentsError when conflicting arguments are used.
		"""
		# start by setting all passed keyword arguments as class attributes
		for (k,v) in kwargs.iteritems():
			setattr(self, k, v)

		# Check if the passed arguments are sane	
		if hasattr(self, 'dirname') and hasattr(self, 'fnames'):
			raise IncompatibleArgumentsError("Incompatible arguments: expect either 'dirname' or 'fnames' when initializing class {0}.".format(type(self).__name__))

		# Check for the filter arg
		if not hasattr(self, 'filter'):
			self.filter='*'

		if hasattr(self, 'fnames'):
			# set fnames here.
			self.dataFiles=self.fnames
			delattr(self, 'fnames')
		else:
			try:
				if hasattr(self, 'dirname') and hasattr(self,'nfiles'):
					# N files from a directory
					self.dataFiles=glob.glob(str(self.dirname)+"/"+str(self.filter))[:int(self.nfiles)]
					delattr(self, 'dirname')
					delattr(self, 'nfiles')
				elif hasattr(self, 'dirname'):
					# all files from a directory
					self.dataFiles=glob.glob(str(self.dirname)+"/"+str(self.filter))
					delattr(self, 'dirname')
				else:
					raise IncompatibleArgumentsError("Missing arguments: 'dirname' or 'fnames' must be supplied to initialize {0}".format(type(self).__name__))
			except AttributeError, err:
				raise IncompatibleArgumentsError(err)

		# set additional meta-data
		self.nFiles = len(self.dataFiles)
		self.fileFormat='N/A'
		try:
			self.datPath="/".join((self.dataFiles[0].split('/'))[:-1])
		except IndexError, err:
			raise FileNotFoundError("Files not found.")

		# setup data filtering
		if hasattr(self, 'datafilter'):
			self.dataFilter=True
			self.dataFilterObj=self.__setupDataFilter()
		else:
			self.dataFilter=False

		if not hasattr(self, 'dcOffset'):
			self.dcOffset=0.0
		else:
			self.dcOffset=float(self.dcOffset)

		# initialize an empty data pipeline
		self.currDataPipe=np.array([])
		# Track the start point of the queue. This var is used to manage
		# deletion more effectively, by not deleting elements every time 
		# popdata is called. Instead, data is actually deleted when the index
		# exceeds 1 million data points.
		self.currDataIdx=0

		self.initPipe=False

	#################################################################
	# Public API: functions
	#################################################################
	@property
	def FsHz(self):
		if not self.initPipe:
			self.__initPipe()

		if not self.dataFilter:
			return self.Fs
		else:
			return self.dataFilterObj.filterFs

	def popdata(self, n):
		"""
			Pop data points from self.currDataPipe. This function uses recursion 
			to automatically read data files when the queue length is shorter
			than the requested data points. When all data files are read, an
			EmptyDataPipeError is thrown.

			Args:
				n number of requested data points
			Returns:
				numpy array with requested data
			Errors:
				EmptyDataPipeError if the queue has fewer data points than requested.
		"""
		if not self.initPipe:
			self.__initPipe()


		try:
			# Get the elements to return: index to (index+n)
			t=self.currDataPipe[self.currDataIdx:self.currDataIdx+n]-self.dcOffset
			if len(t) < n:
				raise IndexError

			# If the required data points were obtained, update the queue index
			self.currDataIdx+=n
			
			# delete them from the pipe if the index exceeds 1 million
			if self.currDataIdx>1000000:
				self.currDataPipe=np.delete(self.currDataPipe, np.s_[:self.currDataIdx:], axis=0)
				# reset the index
				self.currDataIdx=0

			# return the popped data
			return t
		except IndexError, err:
			fnames=self.popfnames(1)
			if len(fnames) > 0:
				self.appenddata(fnames)
				return self.popdata(n)
			else:
				if len(self.currDataPipe)-self.currDataIdx > 0:
					t=self.currDataPipe[self.currDataIdx:self.currDataIdx+n]
					self.currDataIdx=len(self.currDataPipe)

					return t
				else:
					raise EmptyDataPipeError("End of data.")
	
	def previewdata(self, n):
		"""
			Preview data points in self.currDataPipe. This function is identical in 
			behavior to popdata, except it does not remove data point from the queue.
			Like popdata, it uses recursion to automatically read data files 
			when the queue length is shorter than the requested data points. When all 
			data files are read, an	EmptyDataPipeError is thrown.

			Args:
				n number of requested data points
			Returns:
				numpy array with requested data
			Errors:
				EmptyDataPipeError if the queue has fewer data points than requested.
		"""
		if not self.initPipe:
			self.__initPipe()

		try:
			# Get the elements to return
			t=self.currDataPipe[self.currDataIdx:self.currDataIdx+n]-self.dcOffset
			if len(t) < n:
				raise IndexError
			return t
		except IndexError, err:
			fnames=self.popfnames(1)
			if len(fnames) > 0:
				self.appenddata(fnames)
				return self.previewdata(n)
			else:
				if len(self.currDataPipe)-self.currDataIdx > 0:
					t=self.currDataPipe[self.currDataIdx:self.currDataIdx+n]
					self.currDataIdx=len(self.currDataPipe)

					return t
				else:
					raise EmptyDataPipeError("End of data.")

	def formatsettings(self):
		"""
			Return a formatted string of settings for display
		"""
		fmtstr=""

		# add the filter settings
		if self.dataFilter:
			fmtstr+=self.dataFilterObj.formatsettings()

		fmtstr+='\n\tTrajectory I/O settings: \n'
		fmtstr+='\t\tFiles processed = {0}\n'.format(self.nFiles-len(self.dataFiles))
		fmtstr+='\t\tData path = {0}\n'.format(self.datPath)
		fmtstr+='\t\tFile format = {0}\n'.format(self.fileFormat)
		fmtstr+='\t\tSampling frequency = {0} kHz\n'.format(self.FsHz*1e-3)

		return fmtstr

	#################################################################
	# Private API: Interface functions, implemented by sub-classes.
	# Should not be called from external classes
	#################################################################
	def appenddata(self, fname):
		"""
			Read the specified data file(s) and append its data to the data pipeline. Set 
			a class property FsHz with the sampling frequency in Hz.

			Args:
				fname	list of filenames

			
			See implementations of metaTrajIO for specfic documentation.
		"""
		data=self.readdata(fname)
		if self.dataFilter:
			self.dataFilterObj.filterData(data, self.Fs)
			self.currDataPipe=np.hstack((self.currDataPipe, self.dataFilterObj.filteredData ))
		else:
			self.currDataPipe=np.hstack((self.currDataPipe, data ))
			
	@abstractmethod
	def readdata(self, fname):
		"""
			Read the specified data file(s) and  return the data as an array. Set 
			a class property Fs with the sampling frequency in Hz.

			Args:
				fname	list of filenames
		"""
		pass

	def popfnames(self, n):
		"""
			Pop n filenames from the start of self.dataFiles. If filenames run out, 
			simply return the available names. 
			Args:
				n 	number of requested filenames
			Returns:
				List of filenames if successful, empty list if not files remain
			Errors:
				None
		"""
		poplist=[]
		try:
			[ poplist.append(self.dataFiles.pop(0)) for i in range(n) ]
		except IndexError:
			pass
		return poplist

	#################################################################
	# Private Functions
	#################################################################
	def __initPipe(self):
		# Last, on startup load a single data file to force
		# the sampling frequency FsHz to be set on startup
		fnames=self.popfnames(1)
		if len(fnames) > 0:
			self.appenddata(fnames)
		else:
			raise EmptyDataPipeError("End of data.")

		self.initPipe=True

		# Drop the first 'n' points specified by the start keyword
		if hasattr(self, 'start'):
			n=int( getattr(self, 'start') )
			self.popdata(n-1)


	def __setupDataFilter(self):
		filtsettings=settings.settings( self.datPath ).getSettings(self.datafilter.__name__)
		if filtsettings=={}:
			print "No settings found for '{0}'. Data filtering is disabled".format(str(self.datafilter.__name__))
			self.dataFilter=False
			return

		return self.datafilter(**filtsettings)
