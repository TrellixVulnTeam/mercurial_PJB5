#testcases sshv1 sshv2

#if sshv2
  $ cat >> $HGRCPATH << EOF
  > [experimental]
  > sshpeer.advertise-v2 = true
  > sshserver.support-v2 = true
  > EOF
#endif

This file contains testcases that tend to be related to the wire protocol part
of largefiles.

  $ USERCACHE="$TESTTMP/cache"; export USERCACHE
  $ mkdir "${USERCACHE}"
  $ cat >> $HGRCPATH <<EOF
  > [extensions]
  > largefiles=
  > purge=
  > rebase=
  > transplant=
  > [phases]
  > publish=False
  > [largefiles]
  > minsize=2
  > patterns=glob:**.dat
  > usercache=${USERCACHE}
  > [web]
  > allow-archive = zip
  > [hooks]
  > precommit=sh -c "echo \\"Invoking status precommit hook\\"; hg status"
  > EOF


#if serve
vanilla clients not locked out from largefiles servers on vanilla repos
  $ mkdir r1
  $ cd r1
  $ hg init
  $ echo c1 > f1
  $ hg add f1
  $ hg commit -m "m1"
  Invoking status precommit hook
  A f1
  $ cd ..
  $ hg serve -R r1 -d -p $HGPORT --pid-file hg.pid
  $ cat hg.pid >> $DAEMON_PIDS
  $ hg --config extensions.largefiles=! clone http://localhost:$HGPORT r2
  requesting all changes
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 1 changes to 1 files
  new changesets b6eb3a2e2efe
  updating to branch default
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved

largefiles clients still work with vanilla servers
  $ hg serve --config extensions.largefiles=! -R r1 -d -p $HGPORT1 --pid-file hg.pid
  $ cat hg.pid >> $DAEMON_PIDS
  $ hg clone http://localhost:$HGPORT1 r3
  requesting all changes
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 1 changes to 1 files
  new changesets b6eb3a2e2efe
  updating to branch default
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
#endif

vanilla clients locked out from largefiles http repos
  $ mkdir r4
  $ cd r4
  $ hg init
  $ echo c1 > f1
  $ hg add --large f1
  $ hg commit -m "m1"
  Invoking status precommit hook
  A f1
  $ cd ..

largefiles can be pushed locally (issue3583)
  $ hg init dest
  $ cd r4
  $ hg outgoing ../dest
  comparing with ../dest
  searching for changes
  changeset:   0:639881c12b4c
  tag:         tip
  user:        test
  date:        Thu Jan 01 00:00:00 1970 +0000
  summary:     m1
  
  $ hg push ../dest
  pushing to ../dest
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 1 changes to 1 files

exit code with nothing outgoing (issue3611)
  $ hg outgoing ../dest
  comparing with ../dest
  searching for changes
  no changes found
  [1]
  $ cd ..

#if serve
  $ hg serve -R r4 -d -p $HGPORT2 --pid-file hg.pid
  $ cat hg.pid >> $DAEMON_PIDS
  $ hg --config extensions.largefiles=! clone http://localhost:$HGPORT2 r5
  abort: remote error:
  
  This repository uses the largefiles extension.
  
  Please enable it in your Mercurial config file.
  [255]

used all HGPORTs, kill all daemons
  $ killdaemons.py
#endif

vanilla clients locked out from largefiles ssh repos
  $ hg --config extensions.largefiles=! clone -e "\"$PYTHON\" \"$TESTDIR/dummyssh\"" ssh://user@dummy/r4 r5
  remote: 
  remote: This repository uses the largefiles extension.
  remote: 
  remote: Please enable it in your Mercurial config file.
  remote: 
  remote: -
  abort: remote error
  (check previous remote output)
  [255]

#if serve

largefiles clients refuse to push largefiles repos to vanilla servers
  $ mkdir r6
  $ cd r6
  $ hg init
  $ echo c1 > f1
  $ hg add f1
  $ hg commit -m "m1"
  Invoking status precommit hook
  A f1
  $ cat >> .hg/hgrc <<!
  > [web]
  > push_ssl = false
  > allow_push = *
  > !
  $ cd ..
  $ hg clone r6 r7
  updating to branch default
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
  $ cd r7
  $ echo c2 > f2
  $ hg add --large f2
  $ hg commit -m "m2"
  Invoking status precommit hook
  A f2
  $ hg verify --large
  checking changesets
  checking manifests
  crosschecking files in changesets and manifests
  checking files
  2 files, 2 changesets, 2 total revisions
  searching 1 changesets for largefiles
  verified existence of 1 revisions of 1 largefiles
  $ hg serve --config extensions.largefiles=! -R ../r6 -d -p $HGPORT --pid-file ../hg.pid
  $ cat ../hg.pid >> $DAEMON_PIDS
  $ hg push http://localhost:$HGPORT
  pushing to http://localhost:$HGPORT/
  searching for changes
  abort: http://localhost:$HGPORT/ does not appear to be a largefile store
  [255]
  $ cd ..

putlfile errors are shown (issue3123)
Corrupt the cached largefile in r7 and move it out of the servers usercache
  $ mv r7/.hg/largefiles/4cdac4d8b084d0b599525cf732437fb337d422a8 .
  $ echo 'client side corruption' > r7/.hg/largefiles/4cdac4d8b084d0b599525cf732437fb337d422a8
  $ rm "$USERCACHE/4cdac4d8b084d0b599525cf732437fb337d422a8"
  $ hg init empty
  $ hg serve -R empty -d -p $HGPORT1 --pid-file hg.pid \
  >   --config 'web.allow_push=*' --config web.push_ssl=False
  $ cat hg.pid >> $DAEMON_PIDS
  $ hg push -R r7 http://localhost:$HGPORT1
  pushing to http://localhost:$HGPORT1/
  searching for changes
  remote: largefiles: failed to put 4cdac4d8b084d0b599525cf732437fb337d422a8 into store: largefile contents do not match hash
  abort: remotestore: could not put $TESTTMP/r7/.hg/largefiles/4cdac4d8b084d0b599525cf732437fb337d422a8 to remote store http://localhost:$HGPORT1/
  [255]
  $ mv 4cdac4d8b084d0b599525cf732437fb337d422a8 r7/.hg/largefiles/4cdac4d8b084d0b599525cf732437fb337d422a8
Push of file that exists on server but is corrupted - magic healing would be nice ... but too magic
  $ echo "server side corruption" > empty/.hg/largefiles/4cdac4d8b084d0b599525cf732437fb337d422a8
  $ hg push -R r7 http://localhost:$HGPORT1
  pushing to http://localhost:$HGPORT1/
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 2 changesets with 2 changes to 2 files
  $ cat empty/.hg/largefiles/4cdac4d8b084d0b599525cf732437fb337d422a8
  server side corruption
  $ rm -rf empty

Push a largefiles repository to a served empty repository
  $ hg init r8
  $ echo c3 > r8/f1
  $ hg add --large r8/f1 -R r8
  $ hg commit -m "m1" -R r8
  Invoking status precommit hook
  A f1
  $ hg init empty
  $ hg serve -R empty -d -p $HGPORT2 --pid-file hg.pid \
  >   --config 'web.allow_push=*' --config web.push_ssl=False
  $ cat hg.pid >> $DAEMON_PIDS
  $ rm "${USERCACHE}"/*
  $ hg push -R r8 http://localhost:$HGPORT2/#default
  pushing to http://localhost:$HGPORT2/
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  $ [ -f "${USERCACHE}"/02a439e5c31c526465ab1a0ca1f431f76b827b90 ]
  $ [ -f empty/.hg/largefiles/02a439e5c31c526465ab1a0ca1f431f76b827b90 ]

Clone over http, no largefiles pulled on clone.

  $ hg clone http://localhost:$HGPORT2/#default http-clone -U
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 1 changes to 1 files
  new changesets cf03e5bb9936

Archive contains largefiles
  >>> import os
  >>> import urllib2
  >>> u = 'http://localhost:%s/archive/default.zip' % os.environ['HGPORT2']
  >>> with open('archive.zip', 'w') as f:
  ...     f.write(urllib2.urlopen(u).read())
  $ unzip -t archive.zip
  Archive:  archive.zip
      testing: empty-default/.hg_archival.txt*OK (glob)
      testing: empty-default/f1*OK (glob)
  No errors detected in compressed data of archive.zip.

test 'verify' with remotestore:

  $ rm "${USERCACHE}"/02a439e5c31c526465ab1a0ca1f431f76b827b90
  $ mv empty/.hg/largefiles/02a439e5c31c526465ab1a0ca1f431f76b827b90 .
  $ hg -R http-clone verify --large --lfa
  checking changesets
  checking manifests
  crosschecking files in changesets and manifests
  checking files
  1 files, 1 changesets, 1 total revisions
  searching 1 changesets for largefiles
  changeset 0:cf03e5bb9936: f1 missing
  verified existence of 1 revisions of 1 largefiles
  [1]
  $ mv 02a439e5c31c526465ab1a0ca1f431f76b827b90 empty/.hg/largefiles/
  $ hg -R http-clone -q verify --large --lfa

largefiles pulled on update - a largefile missing on the server:
  $ mv empty/.hg/largefiles/02a439e5c31c526465ab1a0ca1f431f76b827b90 .
  $ hg -R http-clone up --config largefiles.usercache=http-clone-usercache
  getting changed largefiles
  f1: largefile 02a439e5c31c526465ab1a0ca1f431f76b827b90 not available from http://localhost:$HGPORT2/
  0 largefiles updated, 0 removed
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
  $ hg -R http-clone st
  ! f1
  $ hg -R http-clone up -Cqr null

largefiles pulled on update - a largefile corrupted on the server:
  $ echo corruption > empty/.hg/largefiles/02a439e5c31c526465ab1a0ca1f431f76b827b90
  $ hg -R http-clone up --config largefiles.usercache=http-clone-usercache
  getting changed largefiles
  f1: data corruption (expected 02a439e5c31c526465ab1a0ca1f431f76b827b90, got 6a7bb2556144babe3899b25e5428123735bb1e27)
  0 largefiles updated, 0 removed
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
  $ hg -R http-clone st
  ! f1
  $ [ ! -f http-clone/.hg/largefiles/02a439e5c31c526465ab1a0ca1f431f76b827b90 ]
  $ [ ! -f http-clone/f1 ]
  $ [ ! -f http-clone-usercache ]
  $ hg -R http-clone verify --large --lfc
  checking changesets
  checking manifests
  crosschecking files in changesets and manifests
  checking files
  1 files, 1 changesets, 1 total revisions
  searching 1 changesets for largefiles
  verified contents of 1 revisions of 1 largefiles
  $ hg -R http-clone up -Cqr null

largefiles pulled on update - no server side problems:
  $ mv 02a439e5c31c526465ab1a0ca1f431f76b827b90 empty/.hg/largefiles/
  $ hg -R http-clone --debug up --config largefiles.usercache=http-clone-usercache --config progress.debug=true
  resolving manifests
   branchmerge: False, force: False, partial: False
   ancestor: 000000000000, local: 000000000000+, remote: cf03e5bb9936
   .hglf/f1: remote created -> g
  getting .hglf/f1
  updating: .hglf/f1 1/1 files (100.00%)
  getting changed largefiles
  using http://localhost:$HGPORT2/
  sending capabilities command
  sending statlfile command
  getting largefiles: 0/1 files (0.00%)
  getting f1:02a439e5c31c526465ab1a0ca1f431f76b827b90
  sending getlfile command
  found 02a439e5c31c526465ab1a0ca1f431f76b827b90 in store
  1 largefiles updated, 0 removed
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved

  $ ls http-clone-usercache/*
  http-clone-usercache/02a439e5c31c526465ab1a0ca1f431f76b827b90

  $ rm -rf empty http-clone*

used all HGPORTs, kill all daemons
  $ killdaemons.py

largefiles should batch verify remote calls

  $ hg init batchverifymain
  $ cd batchverifymain
  $ echo "aaa" >> a
  $ hg add --large a
  $ hg commit -m "a"
  Invoking status precommit hook
  A a
  $ echo "bbb" >> b
  $ hg add --large b
  $ hg commit -m "b"
  Invoking status precommit hook
  A b
  $ cd ..
  $ hg serve -R batchverifymain -d -p $HGPORT --pid-file hg.pid \
  > -A access.log
  $ cat hg.pid >> $DAEMON_PIDS
  $ hg clone --noupdate http://localhost:$HGPORT batchverifyclone
  requesting all changes
  adding changesets
  adding manifests
  adding file changes
  added 2 changesets with 2 changes to 2 files
  new changesets 567253b0f523:04d19c27a332
  $ hg -R batchverifyclone verify --large --lfa
  checking changesets
  checking manifests
  crosschecking files in changesets and manifests
  checking files
  2 files, 2 changesets, 2 total revisions
  searching 2 changesets for largefiles
  verified existence of 2 revisions of 2 largefiles
  $ tail -1 access.log
  $LOCALIP - - [$LOGDATE$] "GET /?cmd=batch HTTP/1.1" 200 - x-hgarg-1:cmds=statlfile+sha%3D972a1a11f19934401291cc99117ec614933374ce%3Bstatlfile+sha%3Dc801c9cfe94400963fcb683246217d5db77f9a9a x-hgproto-1:0.1 0.2 comp=$USUAL_COMPRESSIONS$ partial-pull (glob)
  $ hg -R batchverifyclone update
  getting changed largefiles
  2 largefiles updated, 0 removed
  2 files updated, 0 files merged, 0 files removed, 0 files unresolved

Clear log file before next test

  $ printf "" > access.log

Verify should check file on remote server only when file is not
available locally.

  $ echo "ccc" >> batchverifymain/c
  $ hg -R batchverifymain status
  ? c
  $ hg -R batchverifymain add --large batchverifymain/c
  $ hg -R batchverifymain commit -m "c"
  Invoking status precommit hook
  A c
  $ hg -R batchverifyclone pull
  pulling from http://localhost:$HGPORT/
  searching for changes
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 1 changes to 1 files
  new changesets 6bba8cb6935d
  (run 'hg update' to get a working copy)
  $ hg -R batchverifyclone verify --lfa
  checking changesets
  checking manifests
  crosschecking files in changesets and manifests
  checking files
  3 files, 3 changesets, 3 total revisions
  searching 3 changesets for largefiles
  verified existence of 3 revisions of 3 largefiles
  $ tail -1 access.log
  $LOCALIP - - [$LOGDATE$] "GET /?cmd=statlfile HTTP/1.1" 200 - x-hgarg-1:sha=c8559c3c9cfb42131794b7d8009230403b9b454c x-hgproto-1:0.1 0.2 comp=$USUAL_COMPRESSIONS$ partial-pull (glob)

  $ killdaemons.py

largefiles should not ask for password again after successful authorization

  $ hg init credentialmain
  $ cd credentialmain
  $ echo "aaa" >> a
  $ hg add --large a
  $ hg commit -m "a"
  Invoking status precommit hook
  A a

Before running server clear the user cache to force clone to download
a large file from the server rather than to get it from the cache

  $ rm "${USERCACHE}"/*

  $ cd ..
  $ cat << EOT > userpass.py
  > import base64
  > from mercurial.hgweb import common
  > def perform_authentication(hgweb, req, op):
  >     auth = req.headers.get('Authorization')
  >     if not auth:
  >         raise common.ErrorResponse(common.HTTP_UNAUTHORIZED, 'who',
  >                 [('WWW-Authenticate', 'Basic Realm="mercurial"')])
  >     if base64.b64decode(auth.split()[1]).split(':', 1) != ['user', 'pass']:
  >         raise common.ErrorResponse(common.HTTP_FORBIDDEN, 'no')
  > def extsetup():
  >     common.permhooks.insert(0, perform_authentication)
  > EOT
  $ hg serve --config extensions.x=userpass.py -R credentialmain \
  >          -d -p $HGPORT --pid-file hg.pid -A access.log
  $ cat hg.pid >> $DAEMON_PIDS
  $ cat << EOF > get_pass.py
  > import getpass
  > def newgetpass(arg):
  >   return "pass"
  > getpass.getpass = newgetpass
  > EOF
  $ hg clone --config ui.interactive=true --config extensions.getpass=get_pass.py \
  >          http://user@localhost:$HGPORT credentialclone
  http authorization required for http://localhost:$HGPORT/
  realm: mercurial
  user: user
  password: requesting all changes
  adding changesets
  adding manifests
  adding file changes
  added 1 changesets with 1 changes to 1 files
  new changesets 567253b0f523
  updating to branch default
  getting changed largefiles
  1 largefiles updated, 0 removed
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved

  $ killdaemons.py
  $ rm hg.pid access.log

#endif
