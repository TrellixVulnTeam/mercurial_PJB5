# split.py - split a changeset into smaller ones
#
# Copyright 2015 Laurent Charignon <lcharignon@fb.com>
# Copyright 2017 Facebook, Inc.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.
"""command to split a changeset into smaller ones (EXPERIMENTAL)"""

from __future__ import absolute_import

from mercurial.i18n import _

from mercurial.node import (
    nullid,
    short,
)

from mercurial import (
    bookmarks,
    cmdutil,
    commands,
    error,
    hg,
    obsolete,
    phases,
    pycompat,
    registrar,
    revsetlang,
    scmutil,
)

# allow people to use split without explicitly enabling rebase extension
from . import rebase

cmdtable = {}
command = registrar.command(cmdtable)

# Note for extension authors: ONLY specify testedwith = 'ships-with-hg-core' for
# extensions which SHIP WITH MERCURIAL. Non-mainline extensions should
# be specifying the version(s) of Mercurial they are tested with, or
# leave the attribute unspecified.
testedwith = b'ships-with-hg-core'


@command(
    b'split',
    [
        (b'r', b'rev', b'', _(b"revision to split"), _(b'REV')),
        (b'', b'rebase', True, _(b'rebase descendants after split')),
    ]
    + cmdutil.commitopts2,
    _(b'hg split [--no-rebase] [[-r] REV]'),
    helpcategory=command.CATEGORY_CHANGE_MANAGEMENT,
    helpbasic=True,
)
def split(ui, repo, *revs, **opts):
    """split a changeset into smaller ones

    Repeatedly prompt changes and commit message for new changesets until there
    is nothing left in the original changeset.

    If --rev was not given, split the working directory parent.

    By default, rebase connected non-obsoleted descendants onto the new
    changeset. Use --no-rebase to avoid the rebase.
    """
    opts = pycompat.byteskwargs(opts)
    revlist = []
    if opts.get(b'rev'):
        revlist.append(opts.get(b'rev'))
    revlist.extend(revs)
    with repo.wlock(), repo.lock(), repo.transaction(b'split') as tr:
        revs = scmutil.revrange(repo, revlist or [b'.'])
        if len(revs) > 1:
            raise error.Abort(_(b'cannot split multiple revisions'))

        rev = revs.first()
        ctx = repo[rev]
        if rev is None or ctx.node() == nullid:
            ui.status(_(b'nothing to split\n'))
            return 1
        if ctx.node() is None:
            raise error.Abort(_(b'cannot split working directory'))

        # rewriteutil.precheck is not very useful here because:
        # 1. null check is done above and it's more friendly to return 1
        #    instead of abort
        # 2. mergestate check is done below by cmdutil.bailifchanged
        # 3. unstable check is more complex here because of --rebase
        #
        # So only "public" check is useful and it's checked directly here.
        if ctx.phase() == phases.public:
            raise error.Abort(
                _(b'cannot split public changeset'),
                hint=_(b"see 'hg help phases' for details"),
            )

        descendants = list(repo.revs(b'(%d::) - (%d)', rev, rev))
        alloworphaned = obsolete.isenabled(repo, obsolete.allowunstableopt)
        if opts.get(b'rebase'):
            # Skip obsoleted descendants and their descendants so the rebase
            # won't cause conflicts for sure.
            torebase = list(
                repo.revs(
                    b'%ld - (%ld & obsolete())::', descendants, descendants
                )
            )
            if not alloworphaned and len(torebase) != len(descendants):
                raise error.Abort(
                    _(b'split would leave orphaned changesets behind')
                )
        else:
            if not alloworphaned and descendants:
                raise error.Abort(
                    _(b'cannot split changeset with children without rebase')
                )
            torebase = ()

        if len(ctx.parents()) > 1:
            raise error.Abort(_(b'cannot split a merge changeset'))

        cmdutil.bailifchanged(repo)

        # Deactivate bookmark temporarily so it won't get moved unintentionally
        bname = repo._activebookmark
        if bname and repo._bookmarks[bname] != ctx.node():
            bookmarks.deactivate(repo)

        wnode = repo[b'.'].node()
        top = None
        try:
            top = dosplit(ui, repo, tr, ctx, opts)
        finally:
            # top is None: split failed, need update --clean recovery.
            # wnode == ctx.node(): wnode split, no need to update.
            if top is None or wnode != ctx.node():
                hg.clean(repo, wnode, show_stats=False)
            if bname:
                bookmarks.activate(repo, bname)
        if torebase and top:
            dorebase(ui, repo, torebase, top)


def dosplit(ui, repo, tr, ctx, opts):
    committed = []  # [ctx]

    # Set working parent to ctx.p1(), and keep working copy as ctx's content
    if ctx.node() != repo.dirstate.p1():
        hg.clean(repo, ctx.node(), show_stats=False)
    with repo.dirstate.parentchange():
        scmutil.movedirstate(repo, ctx.p1())

    # Any modified, added, removed, deleted result means split is incomplete
    incomplete = lambda repo: any(repo.status()[:4])

    # Main split loop
    while incomplete(repo):
        if committed:
            header = _(
                b'HG: Splitting %s. So far it has been split into:\n'
            ) % short(ctx.node())
            for c in committed:
                firstline = c.description().split(b'\n', 1)[0]
                header += _(b'HG: - %s: %s\n') % (short(c.node()), firstline)
            header += _(
                b'HG: Write commit message for the next split changeset.\n'
            )
        else:
            header = _(
                b'HG: Splitting %s. Write commit message for the '
                b'first split changeset.\n'
            ) % short(ctx.node())
        opts.update(
            {
                b'edit': True,
                b'interactive': True,
                b'message': header + ctx.description(),
            }
        )
        commands.commit(ui, repo, **pycompat.strkwargs(opts))
        newctx = repo[b'.']
        committed.append(newctx)

    if not committed:
        raise error.Abort(_(b'cannot split an empty revision'))

    scmutil.cleanupnodes(
        repo,
        {ctx.node(): [c.node() for c in committed]},
        operation=b'split',
        fixphase=True,
    )

    return committed[-1]


def dorebase(ui, repo, src, destctx):
    rebase.rebase(
        ui,
        repo,
        rev=[revsetlang.formatspec(b'%ld', src)],
        dest=revsetlang.formatspec(b'%d', destctx.rev()),
    )
