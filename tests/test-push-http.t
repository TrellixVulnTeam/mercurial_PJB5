#require killdaemons

  $ hg init test
  $ cd test
  $ echo a > a
  $ hg ci -Ama
  adding a
  $ cd ..
  $ hg clone test test2
  updating to branch default
  1 files updated, 0 files merged, 0 files removed, 0 files unresolved
  $ cd test2
  $ echo a >> a
  $ hg ci -mb
  $ req() {
  >     hg serve -p $HGPORT -d --pid-file=hg.pid -E errors.log
  >     cat hg.pid >> $DAEMON_PIDS
  >     hg --cwd ../test2 push http://localhost:$HGPORT/
  >     exitstatus=$?
  >     killdaemons.py
  >     echo % serve errors
  >     cat errors.log
  >     return $exitstatus
  > }
  $ cd ../test

expect ssl error

  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  abort: HTTP Error 403: ssl required
  % serve errors
  [255]

expect authorization error

  $ echo '[web]' > .hg/hgrc
  $ echo 'push_ssl = false' >> .hg/hgrc
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  abort: authorization failed
  % serve errors
  [255]

expect authorization error: must have authorized user

  $ echo 'allow_push = unperson' >> .hg/hgrc
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  abort: authorization failed
  % serve errors
  [255]

expect success

  $ cat > $TESTTMP/hook.sh <<'EOF'
  > echo "phase-move: $HG_NODE:  $HG_OLDPHASE -> $HG_PHASE"
  > EOF

  $ cat >> .hg/hgrc <<EOF
  > allow_push = *
  > [hooks]
  > changegroup = sh -c "printenv.py changegroup 0"
  > pushkey = sh -c "printenv.py pushkey 0"
  > txnclose-phase.test = sh $TESTTMP/hook.sh 
  > EOF
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: phase-move: cb9a9f314b8b07ba71012fcdbc544b5a4d82ff5b:  draft -> public
  remote: phase-move: ba677d0156c1196c1a699fa53f390dcfc3ce3872:   -> public
  remote: changegroup hook: HG_BUNDLE2=1 HG_HOOKNAME=changegroup HG_HOOKTYPE=changegroup HG_NODE=ba677d0156c1196c1a699fa53f390dcfc3ce3872 HG_NODE_LAST=ba677d0156c1196c1a699fa53f390dcfc3ce3872 HG_SOURCE=serve HG_TXNID=TXN:$ID$ HG_URL=remote:http:$LOCALIP: (glob)
  % serve errors
  $ hg rollback
  repository tip rolled back to revision 0 (undo serve)

expect success, server lacks the httpheader capability

  $ CAP=httpheader
  $ . "$TESTDIR/notcapable"
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: phase-move: cb9a9f314b8b07ba71012fcdbc544b5a4d82ff5b:  draft -> public
  remote: phase-move: ba677d0156c1196c1a699fa53f390dcfc3ce3872:   -> public
  remote: changegroup hook: HG_BUNDLE2=1 HG_HOOKNAME=changegroup HG_HOOKTYPE=changegroup HG_NODE=ba677d0156c1196c1a699fa53f390dcfc3ce3872 HG_NODE_LAST=ba677d0156c1196c1a699fa53f390dcfc3ce3872 HG_SOURCE=serve HG_TXNID=TXN:$ID$ HG_URL=remote:http:$LOCALIP: (glob)
  % serve errors
  $ hg rollback
  repository tip rolled back to revision 0 (undo serve)

expect success, server lacks the unbundlehash capability

  $ CAP=unbundlehash
  $ . "$TESTDIR/notcapable"
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: phase-move: cb9a9f314b8b07ba71012fcdbc544b5a4d82ff5b:  draft -> public
  remote: phase-move: ba677d0156c1196c1a699fa53f390dcfc3ce3872:   -> public
  remote: changegroup hook: HG_BUNDLE2=1 HG_HOOKNAME=changegroup HG_HOOKTYPE=changegroup HG_NODE=ba677d0156c1196c1a699fa53f390dcfc3ce3872 HG_NODE_LAST=ba677d0156c1196c1a699fa53f390dcfc3ce3872 HG_SOURCE=serve HG_TXNID=TXN:$ID$ HG_URL=remote:http:$LOCALIP: (glob)
  % serve errors
  $ hg rollback
  repository tip rolled back to revision 0 (undo serve)

expect push success, phase change failure

  $ cat > .hg/hgrc <<EOF
  > [web]
  > push_ssl = false
  > allow_push = *
  > [hooks]
  > prepushkey = sh -c "printenv.py prepushkey 1"
  > [devel]
  > legacy.exchange=phases
  > EOF
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  remote: prepushkey hook: HG_BUNDLE2=1 HG_HOOKNAME=prepushkey HG_HOOKTYPE=prepushkey HG_KEY=ba677d0156c1196c1a699fa53f390dcfc3ce3872 HG_NAMESPACE=phases HG_NEW=0 HG_NODE=ba677d0156c1196c1a699fa53f390dcfc3ce3872 HG_NODE_LAST=ba677d0156c1196c1a699fa53f390dcfc3ce3872 HG_OLD=1 HG_PENDING=$TESTTMP/test HG_PHASES_MOVED=1 HG_SOURCE=serve HG_TXNID=TXN:$ID$ HG_URL=remote:http:$LOCALIP: (glob)
  remote: pushkey-abort: prepushkey hook exited with status 1
  remote: transaction abort!
  remote: rollback completed
  abort: updating ba677d0156c1 to public failed
  % serve errors
  [255]

expect phase change success

  $ cat >> .hg/hgrc <<EOF
  > prepushkey = sh -c "printenv.py prepushkey 0"
  > [devel]
  > legacy.exchange=
  > EOF
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  % serve errors
  $ hg rollback
  repository tip rolled back to revision 0 (undo serve)

expect authorization error: all users denied

  $ echo '[web]' > .hg/hgrc
  $ echo 'push_ssl = false' >> .hg/hgrc
  $ echo 'deny_push = *' >> .hg/hgrc
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  abort: authorization failed
  % serve errors
  [255]

expect authorization error: some users denied, users must be authenticated

  $ echo 'deny_push = unperson' >> .hg/hgrc
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  abort: authorization failed
  % serve errors
  [255]

  $ cat > .hg/hgrc <<EOF
  > [web]
  > push_ssl = false
  > allow_push = *
  > [experimental]
  > httppostargs=true
  > EOF
  $ req
  pushing to http://localhost:$HGPORT/
  searching for changes
  remote: adding changesets
  remote: adding manifests
  remote: adding file changes
  remote: added 1 changesets with 1 changes to 1 files
  % serve errors

  $ cd ..
