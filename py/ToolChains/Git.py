# EMACS settings: -*-	tab-width: 2; indent-tabs-mode: t; python-indent-offset: 2 -*-
# vim: tabstop=2:shiftwidth=2:noexpandtab
# kate: tab-width 2; replace-tabs off; indent-width 2;
#
# ==============================================================================
# Authors:          Patrick Lehmann
#                   Martin Zabel
#										Thomas B. Preusser
#
# Python Class:      Git specific classes
#
# Description:
# ------------------------------------
#		TODO:
#		-
#		-
#
# License:
# ==============================================================================
# Copyright 2007-2016 Technische Universitaet Dresden - Germany
#                     Chair of VLSI-Design, Diagnostics and Architecture
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
#
# load dependencies
from pathlib              import Path
from re                   import compile as re_compile
from subprocess           import check_output, CalledProcessError
from os                   import environ
from shutil               import copy as shutil_copy

from Base.Exceptions      import PlatformNotSupportedException, CommonException
from Base.Configuration   import Configuration as BaseConfiguration, ConfigurationException, SkipConfigurationException
from Base.Executable      import Executable, ExecutableArgument, CommandLineArgumentList, CommandArgument, LongFlagArgument, ValuedFlagArgument, StringArgument, \
	LongValuedFlagArgument, LongTupleArgument
from Base.ToolChain       import ToolChainException


__api__ = [
	'GitException',
	'Configuration',
	'GitMixIn',
	'Git',
	'GitSCM',
	'GitRevParse',
	'GitRevList',
	'GitDescribe',
	'GitConfig'
]
__all__ = __api__


class GitException(ToolChainException):
	pass


class Configuration(BaseConfiguration):
	_vendor =      "Git SCM"
	_toolName =    "Git"
	_section =     "INSTALL.Git"
	_template = {
		"Windows": {
			_section: {
				"Version":                "2.8.2",
				"InstallationDirectory":  "C:/Program Files/Git",
				"BinaryDirectory":        "${InstallationDirectory}/cmd"
			}
		},
		"Linux": {
			_section: {
				"Version":                "2.8.1",
				"InstallationDirectory":  "/usr/bin",
				"BinaryDirectory":        "${InstallationDirectory}"
			}
		}
	}

	def __init__(self, host):
		super().__init__(host)

		self._git = None

	def ConfigureForAll(self):
		try:
			if (not self._AskInstalled("Is Git installed on your system?")):
				self.ClearSection()
			else:
				self._host.PoCConfig[self._section]['Version'] = self._template[self._host.Platform][self._section]['Version']
				self._ConfigureInstallationDirectory()
				binPath = self._ConfigureBinaryDirectory()
				self.__WriteGitSection(binPath)
		except ConfigurationException:
			self.ClearSection()
			raise

		if (len(self._host.PoCConfig['INSTALL.Git']) == 0):
			self._host.LogNormal("  Skipping Git setup. Not Git installation found.")
			return

		try:
			binaryDirectoryPath = binPath
			self._git = Git(self._host.Platform, self._host.DryRun, binaryDirectoryPath, logger=self._host.Logger)
		except Exception as ex:
			self._host.LogWarning(str(ex))

		if (not self.__IsUnderGitControl()):
			self._host.LogNormal("Skipping Git setup. This directory is not under Git control.")
			return

		if (not self._AskDoInstall("Install Git mechanisms for PoC developers?")):
			self.__UninstallGitFilters()
			self.__UninstallGitHooks()
			return

		if (self._AskInstalled("Install Git filters?")):
			self.__InstallGitFilters()
		if (self._AskInstalled("Install Git hooks?")):
			self.__InstallGitHooks()

	def _GetDefaultInstallationDirectory(self):
		if (self._host.Platform == "Windows"):
			# TODO: extract to base class -> provide a SearchInPath method
			envPath = environ.get('PATH')
			for pathItem in envPath.split(";"):
				binaryDirectoryPath = Path(pathItem)
				gitPath =             binaryDirectoryPath / "git.exe"
				if gitPath.exists():
					return binaryDirectoryPath.parent.as_posix()
		elif (self._host.Platform in ["Linux", "Darwin"]):
			try:
				name = check_output(["which", "git"], universal_newlines=True).strip()
				if name != "":
					return Path(name).parent.as_posix()
			except CalledProcessError:
				pass  # `which` returns non-zero exit code if GHDL is not in PATH

		return super()._GetDefaultInstallationDirectory()

	def _AskDoInstall(self, question):
		isInstalled = input("  " + question + " [y/N/p]: ")
		isInstalled = isInstalled if isInstalled != "" else "N"
		if (isInstalled in ['p', 'P']):
			raise SkipConfigurationException()
		elif (isInstalled in ['n', 'N']):
			return False
		elif (isInstalled in ['y', 'Y']):
			return True
		else:
			raise ConfigurationException("Unsupported choice '{0}'".format(isInstalled))

	def __WriteGitSection(self, binPath):
		if (self._host.Platform == "Windows"):
			gitPath = binPath / "git.exe"
		else:
			gitPath = binPath / "git"

		if not gitPath.exists():
			raise ConfigurationException("Executable '{0!s}' not found.".format(gitPath)) from FileNotFoundError(str(gitPath))

		# get version and backend
		output = check_output([str(gitPath), "--version"], universal_newlines=True)
		version = None
		versionRegExpStr = r"^git version (\d+\.\d+\.\d+).*"
		versionRegExp = re_compile(versionRegExpStr)
		for line in output.split('\n'):
			if version is None:
				match = versionRegExp.match(line)
				if match is not None:
					version = match.group(1)

		if (version is None):
			raise ConfigurationException("Version number not found in '{0!s} --version' output.".format(gitPath))

		self._host.PoCConfig[self._section]['Version'] = version

	def __IsUnderGitControl(self):
		try:
			gitRevParse = self._git.GetGitRevParse()
			gitRevParse.RevParseParameters[gitRevParse.SwitchInsideWorkingTree] = True
			output = gitRevParse.Execute()
			return (output == "true")
		except CommonException:
			return False

	def __UninstallGitFilters(self):
		self._host.LogNormal("  Uninstalling Git filters...")

		for fileFormat in [None, "rest", "vhdl"]:
			filterName = "filter.normalize"
			if (fileFormat is not None):
				filterName += "_" + fileFormat

			try:
				git = self._git.GetGitConfig()
				git.ConfigParameters[git.SwitchRemoveSection] =   True
				git.ConfigParameters[git.ValueFilterParameters] = filterName
				git.Execute()
			except CommonException:
				self._host.LogWarning("    Error while removing section {0}.".format(filterName))

	def __InstallGitFilters(self):
		self._host.LogNormal("  Installing Git filters...")

		normalizeScript =     "tools/git/filters/normalize.pl"
		pocInstallationPath = Path(self._host.PoCConfig['INSTALL.PoC']['InstallationDirectory'])
		normalizeScriptPath = pocInstallationPath / normalizeScript

		if (not normalizeScriptPath.exists()):
			raise ConfigurationException("Normalize script '{0!s}' not found.".format(normalizeScriptPath)) from FileNotFoundError(str(normalizeScriptPath))

		try:
			commonCleanParameters =   normalizeScript + " clean"
			commonSmudgeParameters =  normalizeScript + " smudge"

			for fileFormat in [None, "rest", "vhdl"]:
				filterName =        "normalize"
				cleanParameters =   commonCleanParameters
				smudgeParameters =  commonSmudgeParameters

				if (fileFormat is not None):
					filterName +=       "_" + fileFormat
					cleanParameters +=  " " + fileFormat
					smudgeParameters += " " + fileFormat

				git = self._git.GetGitConfig()
				git.ConfigParameters[git.ValueFilterClean] =      filterName
				git.ConfigParameters[git.ValueFilterParameters] = cleanParameters
				git.Execute()

				git = self._git.GetGitConfig()
				git.ConfigParameters[git.ValueFilterSmudge] =     filterName
				git.ConfigParameters[git.ValueFilterParameters] = smudgeParameters
				git.Execute()
		except CommonException:
			return False

	def __UninstallGitHooks(self):
		self._host.LogNormal("  Uninstalling Git hooks...")
		pocInstallationPath =   Path(self._host.PoCConfig['INSTALL.PoC']['InstallationDirectory'])
		hookRunnerPath =        pocInstallationPath / "tools/git/hooks/run-hook.sh"

		gitDirectoryPath =      self.__GetGitDirectory()
		gitHookDirectoryPath =  gitDirectoryPath / "hooks"

		for hookName in ["pre-commit"]:
			gitHookPath = gitHookDirectoryPath / hookName
			if gitHookPath.exists():
				if (gitHookPath.is_symlink() and (gitHookPath.resolve() == hookRunnerPath)):
					self._host.LogNormal("  '{0}' hook is configured for PoC. Deleting.".format(hookName))
					try:
						gitHookPath.unlink()
					except OSError as ex:
						raise ConfigurationException("Cannot remove '{0!s}'.".format(gitHookPath)) from ex
				else:
					# TODO: check if file was copied -> Hash compare?
					self._host.LogWarning("  '{0}' hook is in use by another script. Skipping.".format(hookName))

	def __InstallGitHooks(self):
		self._host.LogNormal("  Installing Git hooks...")
		pocInstallationPath =   Path(self._host.PoCConfig['INSTALL.PoC']['InstallationDirectory'])
		hookRunnerPath =        pocInstallationPath / "tools/git/hooks/run-hook.sh"

		if (not hookRunnerPath.exists()):
			raise ConfigurationException("Runner script '{0!s}' not found.".format(hookRunnerPath)) from FileNotFoundError(str(hookRunnerPath))

		gitDirectoryPath =      self.__GetGitDirectory()
		gitHookDirectoryPath =  gitDirectoryPath / "hooks"

		for hookName in ["pre-commit"]:
			gitHookPath = gitHookDirectoryPath / hookName
			if gitHookPath.exists():
				if (gitHookPath.is_symlink() and (gitHookPath.resolve() == hookRunnerPath)):
					self._host.LogNormal("  '{0}' hook is already configured for PoC.".format(hookName))
				else:
					self._host.LogWarning("  '{0}' hook is already in use by another script.".format(hookName))
			else:
				self._host.LogNormal("  Setting '{0}' hook for PoC...".format(hookName))
				self._host.LogDebug("symlink '{0!s}' -> '{1!s}'.".format(gitHookPath, hookRunnerPath))
				try:
					gitHookPath.symlink_to(hookRunnerPath)
				except OSError as ex:
					# if symlink fails, do a copy as backup solution
					if getattr(ex, 'winerror', None) == 1314:
						self._host.LogDebug("copy '{0!s}' to '{1!s}'.".format(hookRunnerPath, gitHookPath))
						try:
							shutil_copy(str(hookRunnerPath), str(gitHookPath))
						except OSError as ex2:
							raise ConfigurationException() from ex2

	def __GetGitDirectory(self):
		try:
			gitRevParse = self._git.GetGitRevParse()
			gitRevParse.RevParseParameters[gitRevParse.SwitchGitDir] = True
			gitDirectory =      gitRevParse.Execute()
			gitDirectoryPath =  Path(gitDirectory)
		except CommonException as ex:
			raise ConfigurationException() from ex

		# WORKAROUND: GIT REV-PARSE
		# if the Git repository isn't a Git submodule AND the current working
		# directory is the Git top-level directory, then 'rev-parse' returns a
		# relative path, otherwise the path is already absolute.
		if (not gitDirectoryPath.is_absolute()):
			pocInstallationPath = Path(self._host.PoCConfig['INSTALL.PoC']['InstallationDirectory'])
			gitDirectoryPath =    pocInstallationPath / gitDirectoryPath

		return gitDirectoryPath


class GitMixIn:
	def __init__(self, platform, dryrun, binaryDirectoryPath, logger=None):
		self._platform =            platform
		self._dryrun =              dryrun
		self._binaryDirectoryPath = binaryDirectoryPath
		# self._version =             version
		self._Logger =              logger


class Git(GitMixIn):
	def GetGitRevParse(self):
		git = GitRevParse(self._platform, self._dryrun, self._binaryDirectoryPath, logger=self._Logger)
		git.Clear()
		git.RevParseParameters[GitRevParse.Command] = True

		return git

	def GetGitRevList(self):
		git = GitRevList(self._platform, self._dryrun, self._binaryDirectoryPath, logger=self._Logger)
		git.Clear()
		git.RevListParameters[GitRevList.Command] = True

		return git

	def GetGitDescribe(self):
		git = GitDescribe(self._platform, self._dryrun, self._binaryDirectoryPath, logger=self._Logger)
		git.Clear()
		git.DescribeParameters[GitDescribe.Command] = True

		return git

	def GetGitConfig(self):
		git = GitConfig(self._platform, self._dryrun, self._binaryDirectoryPath, logger=self._Logger)
		git.Clear()
		git.ConfigParameters[GitConfig.Command] = True

		return git


class GitSCM(Executable, GitMixIn):
	def __init__(self, platform, dryrun, binaryDirectoryPath, logger=None):
		GitMixIn.__init__(self, platform, dryrun, binaryDirectoryPath, logger=logger)

		if (platform == "Windows"):     executablePath = binaryDirectoryPath / "git.exe"
		elif (platform == "Linux"):     executablePath = binaryDirectoryPath / "git"
		elif (platform == "Darwin"):    executablePath = binaryDirectoryPath / "git"
		else:                           raise PlatformNotSupportedException(platform)
		super().__init__(platform, dryrun, executablePath, logger=logger)

		self.Parameters[self.Executable] = executablePath

	def Clear(self):
		for param in self.Parameters:
			if (param is not self.Executable):
				self.Parameters[param] = None

	class Executable(metaclass=ExecutableArgument):
		pass

	class Switch_Version(metaclass=LongFlagArgument):
		_name = "version"

	Parameters = CommandLineArgumentList(
		Executable,
		Switch_Version
	)


class GitRevParse(GitSCM):
	def Clear(self):
		super().Clear()
		for param in self.RevParseParameters:
			# if isinstance(param, ExecutableArgument):
			# 	print("{0}".format(param.Value))
			# elif isinstance(param, NamedCommandLineArgument):
			# 	print("{0}".format(param.Name))
			if (param is not self.Command):
				# print("  clearing: {0} = {1} to None".format(param.Name, param.Value))
				self.RevParseParameters[param] = None

	class Command(metaclass=CommandArgument):
		_name = "rev-parse"

	class SwitchInsideWorkingTree(metaclass=LongFlagArgument):
		_name = "is-inside-work-tree"

	class SwitchShowTopLevel(metaclass=LongFlagArgument):
		_name = "show-toplevel"

	class SwitchGitDir(metaclass=LongFlagArgument):
		_name = "git-dir"

	RevParseParameters = CommandLineArgumentList(
		Command,
		SwitchInsideWorkingTree,
		SwitchShowTopLevel,
		SwitchGitDir
	)

	def Execute(self):
		parameterList = self.Parameters.ToArgumentList()
		parameterList += self.RevParseParameters.ToArgumentList()
		self.LogVerbose("command: {0}".format(" ".join(parameterList)))

		if (self._dryrun):
			self.LogDryRun("Start process: {0}".format(" ".join(parameterList)))
			return

		try:
			self.StartProcess(parameterList)
		except Exception as ex:
			raise GitException("Failed to launch Git.") from ex

		# FIXME: Replace GetReader with a shorter call to e.g. GetLine and/or GetLines
		output = ""
		for line in self.GetReader():
			output += line

		return output

class GitRevList(GitSCM):
	def Clear(self):
		super().Clear()
		for param in self.RevListParameters:
			# if isinstance(param, ExecutableArgument):
			# 	print("{0}".format(param.Value))
			# elif isinstance(param, NamedCommandLineArgument):
			# 	print("{0}".format(param.Name))
			if (param is not self.Command):
				# print("  clearing: {0} = {1} to None".format(param.Name, param.Value))
				self.RevListParameters[param] = None

	class Command(metaclass=CommandArgument):
		_name = "rev-list"

	class SwitchTags(metaclass=LongFlagArgument):
		_name = "tags"

	class SwitchMaxCount(metaclass=LongValuedFlagArgument):
		_name = "max-count"

	RevListParameters = CommandLineArgumentList(
		Command,
		SwitchTags,
		SwitchMaxCount
	)

	def Execute(self):
		parameterList = self.Parameters.ToArgumentList()
		parameterList += self.RevListParameters.ToArgumentList()
		self.LogVerbose("command: {0}".format(" ".join(parameterList)))

		if (self._dryrun):
			self.LogDryRun("Start process: {0}".format(" ".join(parameterList)))
			return

		try:
			self.StartProcess(parameterList)
		except Exception as ex:
			raise GitException("Failed to launch Git.") from ex

		# FIXME: Replace GetReader with a shorter call to e.g. GetLine and/or GetLines
		output = ""
		for line in self.GetReader():
			output += line

		return output

class GitDescribe(GitSCM):
	def Clear(self):
		super().Clear()
		for param in self.DescribeParameters:
			# if isinstance(param, ExecutableArgument):
			# 	print("{0}".format(param.Value))
			# elif isinstance(param, NamedCommandLineArgument):
			# 	print("{0}".format(param.Name))
			if (param is not self.Command):
				# print("  clearing: {0} = {1} to None".format(param.Name, param.Value))
				self.DescribeParameters[param] = None

	class Command(metaclass=CommandArgument):
		_name = "describe"

	class SwitchTags(metaclass=LongTupleArgument):
		_name = "tags"

	DescribeParameters = CommandLineArgumentList(
		Command,
		SwitchTags
	)

	def Execute(self):
		parameterList = self.Parameters.ToArgumentList()
		parameterList += self.DescribeParameters.ToArgumentList()
		self.LogVerbose("command: {0}".format(" ".join(parameterList)))

		if (self._dryrun):
			self.LogDryRun("Start process: {0}".format(" ".join(parameterList)))
			return

		try:
			self.StartProcess(parameterList)
		except Exception as ex:
			raise GitException("Failed to launch Git.") from ex

		# FIXME: Replace GetReader with a shorter call to e.g. GetLine and/or GetLines
		output = ""
		for line in self.GetReader():
			output += line

		return output

class GitConfig(GitSCM):
	def Clear(self):
		super().Clear()
		for param in self.ConfigParameters:
			# if isinstance(param, ExecutableArgument):
				# print("{0}".format(param.Value))
			# elif isinstance(param, NamedCommandLineArgument):
				# print("{0}".format(param.Name))
			if (param is not self.Command):
				# print("  clearing: {0} = {1} to None".format(param.Name, param.Value))
				self.ConfigParameters[param] = None

	class Command(metaclass=CommandArgument):
		_name =     "config"

	class SwitchUnset(metaclass=LongFlagArgument):
		_name =     "unset"

	class SwitchRemoveSection(metaclass=LongFlagArgument):
		_name =     "remove-section"

	class ValueFilterClean(metaclass=ValuedFlagArgument):
		_name =     "clean"
		_pattern =  "filter.{1}.{0}"

	class ValueFilterSmudge(metaclass=ValuedFlagArgument):
		_name =     "smudge"
		_pattern =  "filter.{1}.{0}"

	class ValueFilterParameters(metaclass=StringArgument):
		pass

	ConfigParameters = CommandLineArgumentList(
		Command,
		SwitchUnset,
		SwitchRemoveSection,
		ValueFilterClean,
		ValueFilterSmudge,
		ValueFilterParameters
	)

	def Execute(self):
		parameterList = self.Parameters.ToArgumentList()
		parameterList += self.ConfigParameters.ToArgumentList()
		self.LogVerbose("command: {0}".format(" ".join(parameterList)))

		if (self._dryrun):
			self.LogDryRun("Start process: {0}".format(" ".join(parameterList)))
			return

		try:
			self.StartProcess(parameterList)
		except Exception as ex:
			raise GitException("Failed to launch Git.") from ex

		# FIXME: Replace GetReader with a shorter call to e.g. GetLine and/or GetLines
		output = ""
		for line in self.GetReader():
			output += line

		return output

	# LOCAL = git rev-parse @
	# PS G:\git\PoC> git rev-parse "@"
	# 9c05494ef52c276dabec69dbf734a22f65939305

	# REMOTE = git rev-parse @{u}
	# PS G:\git\PoC> git rev-parse "@{u}"
	# 0ff166a40010c1b85a5ab655eea0148474f680c6

	# MERGEBASE = git merge-base @ @{u}
	# PS G:\git\PoC> git merge-base "@" "@{u}"
	# 0ff166a40010c1b85a5ab655eea0148474f680c6

	# if (local == remote):   return "Up-to-date"
	# elif (local == base):   return "Need to pull"
	# elif (remote == base):  return "Need to push"
	# else:                   return "divergent"
