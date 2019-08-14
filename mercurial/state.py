# state.py - writing and reading state files in Mercurial
#
# Copyright 2018 Pulkit Goyal <pulkitmgoyal@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

"""
This file contains class to wrap the state for commands and other
related logic.

All the data related to the command state is stored as dictionary in the object.
The class has methods using which the data can be stored to disk in a file under
.hg/ directory.

We store the data on disk in cbor, for which we use the CBOR format to encode
the data.
"""

from __future__ import absolute_import

from .i18n import _

from . import (
    error,
    util,
)
from .utils import (
    cborutil,
)

class cmdstate(object):
    """a wrapper class to store the state of commands like `rebase`, `graft`,
    `histedit`, `shelve` etc. Extensions can also use this to write state files.

    All the data for the state is stored in the form of key-value pairs in a
    dictionary.

    The class object can write all the data to a file in .hg/ directory and
    can populate the object data reading that file.

    Uses cbor to serialize and deserialize data while writing and reading from
    disk.
    """

    def __init__(self, repo, fname):
        """ repo is the repo object
        fname is the file name in which data should be stored in .hg directory
        """
        self._repo = repo
        self.fname = fname

    def read(self):
        """read the existing state file and return a dict of data stored"""
        return self._read()

    def save(self, version, data):
        """write all the state data stored to .hg/<filename> file

        we use third-party library cbor to serialize data to write in the file.
        """
        if not isinstance(version, int):
            raise error.ProgrammingError("version of state file should be"
                                         " an integer")

        with self._repo.vfs(self.fname, 'wb', atomictemp=True) as fp:
            fp.write('%d\n' % version)
            for chunk in cborutil.streamencode(data):
                fp.write(chunk)

    def _read(self):
        """reads the state file and returns a dictionary which contain
        data in the same format as it was before storing"""
        with self._repo.vfs(self.fname, 'rb') as fp:
            try:
                int(fp.readline())
            except ValueError:
                raise error.CorruptedState("unknown version of state file"
                                           " found")

            return cborutil.decodeall(fp.read())[0]

    def delete(self):
        """drop the state file if exists"""
        util.unlinkpath(self._repo.vfs.join(self.fname), ignoremissing=True)

    def exists(self):
        """check whether the state file exists or not"""
        return self._repo.vfs.exists(self.fname)

class _statecheck(object):
    """a utility class that deals with multistep operations like graft,
       histedit, bisect, update etc and check whether such commands
       are in an unfinished conditition or not and return appropriate message
       and hint.
       It also has the ability to register and determine the states of any new
       multistep operation or multistep command extension.
    """

    def __init__(self, opname, fname, clearable, allowcommit, reportonly,
                 continueflag, stopflag, cmdmsg, cmdhint, statushint,
                 abortfunc, continuefunc):
        self._opname = opname
        self._fname = fname
        self._clearable = clearable
        self._allowcommit = allowcommit
        self._reportonly = reportonly
        self._continueflag = continueflag
        self._stopflag = stopflag
        self._cmdmsg = cmdmsg
        self._cmdhint = cmdhint
        self._statushint = statushint
        self.abortfunc = abortfunc
        self.continuefunc = continuefunc

    def statusmsg(self):
        """returns the hint message corresponding to the command for
        hg status --verbose
        """
        if not self._statushint:
            hint = (_('To continue:    hg %s --continue\n'
                      'To abort:       hg %s --abort') % (self._opname,
                       self._opname))
            if self._stopflag:
                hint = hint + (_('\nTo stop:        hg %s --stop') %
                            (self._opname))
            return hint
        return self._statushint

    def hint(self):
        """returns the hint message corresponding to an interrupted
        operation
        """
        if not self._cmdhint:
                return (_("use 'hg %s --continue' or 'hg %s --abort'") %
                        (self._opname, self._opname))
        return self._cmdhint

    def msg(self):
        """returns the status message corresponding to the command"""
        if not self._cmdmsg:
            return _('%s in progress') % (self._opname)
        return self._cmdmsg

    def continuemsg(self):
        """ returns appropriate continue message corresponding to command"""
        return _('hg %s --continue') % (self._opname)

    def isunfinished(self, repo):
        """determines whether a multi-step operation is in progress
        or not
        """
        if self._opname == 'merge':
            return len(repo[None].parents()) > 1
        else:
            return repo.vfs.exists(self._fname)

# A list of statecheck objects for multistep operations like graft.
_unfinishedstates = []

def addunfinished(opname, fname, clearable=False, allowcommit=False,
                  reportonly=False, continueflag=False, stopflag=False,
                  cmdmsg="", cmdhint="", statushint="", abortfunc=None,
                  continuefunc=None):
    """this registers a new command or operation to unfinishedstates
    opname is the name the command or operation
    fname is the file name in which data should be stored in .hg directory.
    It is None for merge command.
    clearable boolean determines whether or not interrupted states can be
    cleared by running `hg update -C .` which in turn deletes the
    state file.
    allowcommit boolean decides whether commit is allowed during interrupted
    state or not.
    reportonly flag is used for operations like bisect where we just
    need to detect the operation using 'hg status --verbose'
    continueflag is a boolean determines whether or not a command supports
    `--continue` option or not.
    stopflag is a boolean that determines whether or not a command supports
    --stop flag
    cmdmsg is used to pass a different status message in case standard
    message of the format "abort: cmdname in progress" is not desired.
    cmdhint is used to pass a different hint message in case standard
    message of the format "To continue: hg cmdname --continue
    To abort: hg cmdname --abort" is not desired.
    statushint is used to pass a different status message in case standard
    message of the format ('To continue:    hg cmdname --continue'
    'To abort:       hg cmdname --abort') is not desired
    abortfunc stores the function required to abort an unfinished state.
    continuefunc stores the function required to finish an interrupted
    operation.
    """
    statecheckobj = _statecheck(opname, fname, clearable, allowcommit,
                                reportonly, continueflag, stopflag, cmdmsg,
                                cmdhint, statushint, abortfunc, continuefunc)
    if opname == 'merge':
        _unfinishedstates.append(statecheckobj)
    else:
        _unfinishedstates.insert(0, statecheckobj)

addunfinished(
    'update', fname='updatestate', clearable=True,
    cmdmsg=_('last update was interrupted'),
    cmdhint=_("use 'hg update' to get a consistent checkout"),
    statushint=_("To continue:    hg update")
)
addunfinished(
    'bisect', fname='bisect.state', allowcommit=True, reportonly=True,
    statushint=_('To mark the changeset good:    hg bisect --good\n'
                 'To mark the changeset bad:     hg bisect --bad\n'
                 'To abort:                      hg bisect --reset\n')
)

def getrepostate(repo):
    # experimental config: commands.status.skipstates
    skip = set(repo.ui.configlist('commands', 'status.skipstates'))
    for state in _unfinishedstates:
        if state._opname in skip:
            continue
        if state.isunfinished(repo):
            return (state._opname, state.statusmsg())
