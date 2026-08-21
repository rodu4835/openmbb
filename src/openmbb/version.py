"""What this copy can honestly say about its own age.

Built for one job — getting somebody off a build that has gone stale, on a tool
that writes settings to a motorcycle, without the program ever opening a socket.
That job sets the rules this module follows:

  It reports its OWN AGE, never the state of the world. "This copy is 63 days
  old" is a fact about the file on disk: provable, local, and never wrong. "A
  newer version is available" is a fact about a server, and this program has no
  way to learn it. The first may be printed; the second may not, in any wording,
  ever — not "an update is available", not "you are out of date", not "0.24.0 is
  out". A claim the program cannot check is exactly the kind of confident
  wrongness the rest of this codebase exists to refuse.

  A nonsense claim is worse than no claim. A clock behind the release date, a
  clock decades ahead, a missing or hand-edited stamp: every one of those
  produces SILENCE rather than a guess. There is no fallback that invents an
  age, because a machine with a dead CMOS battery would otherwise be told its
  three-day-old build is fifty years old.

  It never phones home to find out. That is not a limitation being worked
  around; it is the point. The trade is stated out loud to the user rather than
  hidden: OpenMBB cannot tell you whether anything newer exists, and it says so
  in the same breath as it tells you how old you are.

  Nothing here is a threshold on quality. An old build is not a broken build,
  and the wording must not imply it is. STALE_AFTER_DAYS is a prompt to look,
  not a verdict.
"""

import datetime as _dt

# How old a copy has to be before the home screen mentions it. Six weeks: long
# enough that a normal release cadence never trips it, short enough to catch the
# drift this exists for (the project's own author ran weeks behind without
# noticing). Not a quality judgement — see the docstring.
STALE_AFTER_DAYS = 45

# Beyond this the host clock is not to be believed rather than the build being
# ancient, so nothing is said at all.
ABSURD_AGE_DAYS = 3650


def release_date(stamp):
    """Parse an ISO release stamp, or None if it is missing or malformed.

    A fork, a hand-edited source tree or a build that never set the stamp all
    land here, and all of them mean "say nothing" rather than "assume today".
    """
    if not stamp:
        return None
    try:
        return _dt.date.fromisoformat(str(stamp).strip())
    except (ValueError, TypeError):
        return None


def age_days(stamp, today=None):
    """Whole days between the release date and `today`, or None if unknowable.

    Negative ages are returned as-is rather than clamped: the caller decides
    what to do with a clock that sits before the release date, and hiding it
    here would make that decision invisible.
    """
    rel = release_date(stamp)
    if rel is None:
        return None
    return ((today or _dt.date.today()) - rel).days


def is_stale(stamp, today=None):
    """Whether this copy is old enough to be worth mentioning.

    False for every uncertain case, which is the whole point: a clock running
    behind the release date, a clock a decade out, or no stamp at all all read
    as "nothing to say" rather than as a warning nobody can act on.
    """
    age = age_days(stamp, today)
    if age is None:
        return False
    return STALE_AFTER_DAYS <= age <= ABSURD_AGE_DAYS


def describe_age(stamp, today=None):
    """"63 days old (released 21 Aug 2026)", or None when it cannot be said."""
    age = age_days(stamp, today)
    rel = release_date(stamp)
    if age is None or rel is None:
        return None
    if age < 0 or age > ABSURD_AGE_DAYS:
        # the host clock disagrees with the build; report the date and no age
        return "released %s" % rel.strftime("%d %b %Y")
    return "%d day%s old (released %s)" % (age, "" if age == 1 else "s",
                                           rel.strftime("%d %b %Y"))


def describe_release(stamp, today=None):
    """"21 Aug 2026 (63 days ago)" for an About box, or "unknown".

    Date first, because this is answering "what am I running" rather than "is
    this old". The age is dropped entirely when the host clock disagrees with
    the build date - reporting "-230 days ago" would be worse than saying
    nothing, and saying nothing here still leaves the date.
    """
    rel = release_date(stamp)
    if rel is None:
        return "unknown"
    shown = rel.strftime("%d %b %Y")
    age = age_days(stamp, today)
    if age is None or age < 0 or age > ABSURD_AGE_DAYS:
        return shown
    if age == 0:
        return "%s (today)" % shown
    return "%s (%d day%s ago)" % (shown, age, "" if age == 1 else "s")


def stale_notice(version, stamp, today=None):
    """The home-screen paragraph, or None when there is nothing to say.

    Every clause is load-bearing. The age is a checkable local fact. The
    admission that OpenMBB cannot tell you what is newer is this project's own
    "unknown is a real answer" idiom turned on itself, and it belongs here
    rather than in a footnote: it is exactly the moment a reader would otherwise
    wonder why nothing warned them.
    """
    if not is_stale(stamp, today):
        return None
    return ("This copy is %s. Whether anything newer exists, OpenMBB cannot "
            "tell you — it makes no network requests. Releases have been "
            "frequent, and this app writes settings to a motorcycle. Worth a "
            "look." % describe_age(stamp, today))
