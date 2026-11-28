import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The link app as a release carries it: router/build.sh --release (built for both processors, signed with a
# Developer ID, notarized, zipped with its links.json), and the installer's link app block, which takes that prebuilt
# app only when every check passes and builds the app on this Mac otherwise.
#
# Nothing here compiles, signs, notarizes or registers anything. build.sh runs from a copy of router/ whose Info.plist
# has a made-up bundle identifier, with stand-in swiftc, lipo, codesign, security, openssl, xcrun, spctl and ioreg on
# a private PATH that holds nothing else but the plain file tools. The installer's link app block is lifted out of
# install.sh and run on its own in a throwaway home, with stand-in swiftc, codesign, xcode-select and sw_vers, and
# always --no-register. The made-up apps are never run.
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))

FINGERPRINT = '0123456789ABCDEF0123456789ABCDEF01234567'
OTHER_FINGERPRINT = 'FEDCBA9876543210FEDCBA9876543210FEDCBA98'
TEAM = 'ABCDE12345'
SIGNER = 'Developer ID Application: Example Maker (%s)' % TEAM
PLIST = ('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
         '<key>CFBundleIdentifier</key><string>com.example.links-test</string>'
         '<key>CFBundleExecutable</key><string>SwitchboardLinks</string>'
         '<key>CFBundleShortVersionString</key><string>0.2.0</string>'
         '<key>CFBundleVersion</key><string>2</string>'
         '<key>LSMinimumSystemVersion</key><string>12.0</string></dict></plist>\n')
SWIFT = '// made up: never compiled here\n'
# The plain file tools build.sh may use, linked into the private PATH. Nothing else from the system is on it.
FILE_TOOLS = ('sh', 'cat', 'cp', 'mkdir', 'rm', 'mv', 'sed', 'grep', 'awk', 'tr', 'head', 'tail', 'mktemp', 'dirname',
              'basename', 'df', 'date', 'shasum', 'ditto', 'chmod', 'wc', 'sort', 'cut', 'ls', 'env', 'uname', 'cmp',
              'find', 'tee', 'expr', 'touch')


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def files(top):
    out = []
    for folder, dirs, names in os.walk(top):
        out += [os.path.relpath(os.path.join(folder, n), top) for n in dirs + names]
    return sorted(out)


def calls(fake):
    path = os.path.join(fake, 'calls')
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return f.read().splitlines()


# ---------- stand-in programs: each writes down how it was called in $FAKE/calls ----------

SWIFTC = r'''#!/bin/sh
echo "swiftc $*" >> "$FAKE/calls"
[ "$1" != --version ] || { echo "Apple Swift version 6.0 (a stand-in)"; exit 0; }
[ ! -f "$FAKE/swiftc-fails" ] || { echo "error: made up" >&2; exit 1; }
out=""
while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done
printf '#!/bin/sh\n# %s\n' "$(cat "$FAKE/compiled-mark" 2>/dev/null || echo compiled)" > "$out"
'''
LIPO = r'''#!/bin/sh
echo "lipo $*" >> "$FAKE/calls"
case "$1" in
  -create) out="$3"; shift 3; cat "$@" > "$out" ;;
  -archs) echo "x86_64 arm64" ;;
esac
'''
CODESIGN = r'''#!/bin/sh
echo "codesign $*" >> "$FAKE/calls"
for a in "$@"; do
  case "$a" in
    --verify) [ -f "$FAKE/verify-fails" ] && { echo "a sealed resource is missing or invalid" >&2; exit 1; }
              echo "valid on disk" >&2; echo "satisfies its Designated Requirement" >&2; exit 0 ;;
    -dv|-d) cat "$FAKE/display" >&2 2>/dev/null; exit 0 ;;
    --sign) [ ! -f "$FAKE/sign-fails" ] || { echo "error: The specified item could not be found." >&2; exit 1; }
            exit 0 ;;
  esac
done
'''
SECURITY = r'''#!/bin/sh
echo "security $*" >> "$FAKE/calls"
case "$1" in
  find-identity) cat "$FAKE/identities" 2>/dev/null; echo "     0 valid identities found" ;;
  find-certificate) cat "$FAKE/certificates" 2>/dev/null ;;
esac
'''
# openssl x509 reads one made-up certificate on its standard input: its body is a name, and $FAKE/<name>.* say what
# the real openssl would print for it.
OPENSSL = r'''#!/bin/sh
echo "openssl $*" >> "$FAKE/calls"
m="$(grep -v -- ----- | head -n 1)"
for a in "$@"; do
  case "$a" in
    -fingerprint) cat "$FAKE/$m.fingerprint"; exit 0 ;;
    -subject) cat "$FAKE/$m.subject"; exit 0 ;;
    -issuer) cat "$FAKE/$m.issuer"; exit 0 ;;
    -enddate) cat "$FAKE/$m.enddate"; exit 0 ;;
    -checkend) shift; while [ "$1" != -checkend ]; do shift; done; n="$2"
               [ -f "$FAKE/$m.ends-within-$n" ] && { echo "Certificate will expire"; exit 1; }
               echo "Certificate will not expire"; exit 0 ;;
  esac
done
'''
XCRUN = r'''#!/bin/sh
echo "xcrun $*" >> "$FAKE/calls"
case "$1 $2" in
  "--find notarytool"|"--find stapler") [ -f "$FAKE/no-$2" ] && { echo "xcrun: error: unable to find utility \"$2\"" >&2; exit 72; }
                                        echo "/made/up/$2" ;;
  "notarytool history") [ -f "$FAKE/no-profile" ] && { echo "Error: No Keychain password item found for profile" >&2; exit 69; }
                        echo "No submission history." ;;
  "notarytool submit") cat "$FAKE/submit" ;;
  "stapler staple") touch "$3/Contents/CodeResources"; echo "The staple and validate action worked!" ;;
  *) exit 1 ;;
esac
'''
SPCTL = r'''#!/bin/sh
echo "spctl $*" >> "$FAKE/calls"
cat "$FAKE/spctl" >&2
'''
IOREG = r'''#!/bin/sh
echo "ioreg $*" >> "$FAKE/calls"
echo '+-o Root  <class IORegistryEntry, id 0x100000100, retain 12>'
echo '    {'
[ -f "$FAKE/locked" ] && echo '      "IOConsoleUsers" = ({"CGSSessionScreenIsLocked"=Yes,"kCGSSessionOnConsoleKey"=Yes})' \
                      || echo '      "IOConsoleUsers" = ({"kCGSSessionOnConsoleKey"=Yes})'
echo '    }'
'''
XCODE_SELECT = r'''#!/bin/sh
echo "/made/up/Developer"
'''
SW_VERS = r'''#!/bin/sh
cat "$FAKE/macos" 2>/dev/null || echo 15.1
'''

DISPLAY = '''Executable=/made/up/Switchboard Links.app/Contents/MacOS/SwitchboardLinks
Identifier=com.example.links-test
Format=app bundle with Mach-O universal (x86_64 arm64)
CodeDirectory v=20500 size=637 flags=0x10000(runtime) hashes=9+7 location=embedded
Authority=%(signer)s
Authority=Developer ID Certification Authority
Authority=Apple Root CA
Timestamp=Nov 1, 2026 at 9:00:00 AM
%(ticket)sTeamIdentifier=%(team)s
Sealed Resources version=2 rules=13 files=1
'''


def display(team=TEAM, signer=SIGNER, stapled=True):
    return DISPLAY % {'team': team, 'signer': signer, 'ticket': 'Notarization Ticket=stapled\n' if stapled else ''}


class ReleaseBuild(unittest.TestCase):
    """sh router/build.sh --release OUTDIR --identity FINGERPRINT [--notarize PROFILE] [--check]."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-release-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.router = os.path.join(self.tmp, 'router')
        os.makedirs(self.router)
        shutil.copy(os.path.join(ROOT, 'router', 'build.sh'), self.router)
        write(os.path.join(self.router, 'Info.plist'), PLIST)
        write(os.path.join(self.router, 'SwitchboardLinks.swift'), SWIFT)
        shutil.copy(os.path.join(ROOT, 'router', 'SwitchboardLinks.icns'), self.router)
        self.router_before = dict((p, os.stat(os.path.join(self.router, p)).st_mtime_ns) for p in files(self.router))
        self.fake = os.path.join(self.tmp, 'fake')
        self.temp = os.path.join(self.tmp, 'temp')
        self.home = os.path.join(self.tmp, 'home')
        self.out = os.path.join(self.tmp, 'release')
        self.bin = os.path.join(self.tmp, 'bin')
        for d in (self.fake, self.temp, self.home, self.bin):
            os.makedirs(d)
        system = '/usr/bin:/bin:/usr/sbin:/sbin'
        for name in FILE_TOOLS:          # a script that runs the real one (a link would lead outside the temp folder)
            write(os.path.join(self.bin, name), '#!/bin/sh\nexec %s "$@"\n' % shutil.which(name, path=system), 0o755)
        for name, text in (('swiftc', SWIFTC), ('lipo', LIPO), ('codesign', CODESIGN), ('security', SECURITY),
                           ('openssl', OPENSSL), ('xcrun', XCRUN), ('spctl', SPCTL), ('ioreg', IOREG)):
            write(os.path.join(self.bin, name), text, 0o755)
        # One Developer ID certificate in the keychain, as `security` and `openssl` report it.
        self.certificate('A', FINGERPRINT, SIGNER, g2=True)
        write(os.path.join(self.fake, 'identities'), '  1) %s "%s"\n' % (FINGERPRINT, SIGNER))
        write(os.path.join(self.fake, 'certificates'), self.pem('A'))
        write(os.path.join(self.fake, 'display'), display(stapled=False))
        write(os.path.join(self.fake, 'submit'), 'Conducting pre-submission checks for SwitchboardLinks.zip\n'
              '  id: 00000000-0000-4000-8000-000000000001\nProcessing complete\n'
              '  id: 00000000-0000-4000-8000-000000000001\n  status: Accepted\n')
        write(os.path.join(self.fake, 'spctl'), '/made/up/Switchboard Links.app: accepted\n'
              'source=Notarized Developer ID\norigin=%s\n' % SIGNER)

    def certificate(self, name, fingerprint, subject_name, g2=True, ends='Feb  1 12:00:00 2028 GMT'):
        colons = ':'.join(fingerprint[i:i + 2] for i in range(0, 40, 2))
        write(os.path.join(self.fake, name + '.fingerprint'), 'SHA1 Fingerprint=%s\n' % colons)
        write(os.path.join(self.fake, name + '.subject'), 'subject= /UID=%s/CN=%s/OU=%s/O=Example Maker/C=US\n'
              % (TEAM, subject_name, TEAM))
        write(os.path.join(self.fake, name + '.issuer'), 'issuer= /CN=Developer ID Certification Authority/OU=%s/'
              'O=Apple Inc./C=US\n' % ('G2' if g2 else 'Apple Certification Authority'))
        write(os.path.join(self.fake, name + '.enddate'), 'notAfter=%s\n' % ends)

    def pem(self, *names):
        return ''.join('-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n' % n for n in names)

    def mark(self, name):
        write(os.path.join(self.fake, name), '')

    def release(self, *args):
        env = {'PATH': self.bin, 'HOME': self.home, 'TMPDIR': self.temp + '/', 'FAKE': self.fake, 'LANG': 'C'}
        return subprocess.run(['/bin/sh', os.path.join(self.router, 'build.sh'), '--release', self.out] + list(args),
                              capture_output=True, text=True, env=env, timeout=120)

    def compiled(self):
        return [c for c in calls(self.fake) if c.startswith('swiftc') and '--version' not in c]

    def signed(self):
        return [c for c in calls(self.fake) if c.startswith('codesign') and '--sign' in c]

    def assertNothingLeft(self, out_too=True):
        if out_too:
            self.assertFalse(os.path.exists(self.out), files(self.tmp))
        self.assertEqual(os.listdir(self.temp), [])
        self.assertEqual(os.listdir(self.home), [])
        self.assertEqual(dict((p, os.stat(os.path.join(self.router, p)).st_mtime_ns) for p in files(self.router)),
                         self.router_before)

    def links_json(self):
        with open(os.path.join(self.out, 'links.json')) as f:
            return json.load(f)

    # --- refusals: one plain line, nothing built, nothing written ---

    def test_no_identity(self):
        r = self.release()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertIn('--identity', r.stderr)
        self.assertIn('security find-identity -v -p codesigning', r.stderr)
        self.assertEqual(self.compiled(), [])
        self.assertNothingLeft()

    def test_a_name_instead_of_a_fingerprint(self):
        r = self.release('--identity', SIGNER)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(r.stderr.strip(), '--identity takes the certificate\'s SHA-1 fingerprint (40 hexadecimal '
                         'digits), not its name: two certificates can carry the same name, and codesign then refuses '
                         'it as ambiguous. List them with: security find-identity -v -p codesigning')
        self.assertEqual(calls(self.fake), [])
        self.assertNothingLeft()

    def test_a_fingerprint_no_keychain_holds(self):
        r = self.release('--identity', OTHER_FINGERPRINT)
        self.assertEqual(r.returncode, 69, r.stdout + r.stderr)
        self.assertIn('No signing identity with the fingerprint %s is in your keychains' % OTHER_FINGERPRINT, r.stderr)
        self.assertEqual((self.compiled(), self.signed()), ([], []))
        self.assertNothingLeft()

    def test_a_locked_screen_stops_notarizing_before_anything_is_built(self):
        self.mark('locked')
        r = self.release('--identity', FINGERPRINT, '--notarize', 'example-profile')
        self.assertEqual(r.returncode, 76, r.stdout + r.stderr)
        self.assertIn('The screen is locked: notarytool can read its saved sign-in only while the screen is unlocked. '
                      'Unlock it and run this again.', r.stderr)
        self.assertEqual((self.compiled(), self.signed()), ([], []))
        self.assertFalse([c for c in calls(self.fake) if c.startswith(('xcrun notarytool', 'xcrun stapler'))])
        self.assertNothingLeft()

    def test_a_missing_tool(self):
        os.remove(os.path.join(self.bin, 'lipo'))
        r = self.release('--identity', '-')
        self.assertEqual(r.returncode, 69, r.stdout + r.stderr)
        self.assertIn('lipo is missing: install Xcode or Apple\'s command line tools (xcode-select --install), then '
                      'run this again.', r.stderr)
        self.assertEqual(self.compiled(), [])
        self.assertNothingLeft()

    def test_notarizing_needs_notarytool_and_a_saved_profile(self):
        self.mark('no-notarytool')
        self.mark('no-profile')
        r = self.release('--identity', FINGERPRINT, '--notarize', 'example-profile')
        self.assertEqual(r.returncode, 69, r.stdout + r.stderr)
        self.assertIn('notarytool is missing', r.stderr)
        self.assertEqual(self.compiled(), [])
        self.assertNothingLeft()

    def test_an_ad_hoc_build_is_never_notarized(self):
        r = self.release('--identity', '-', '--notarize', 'example-profile')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('An ad-hoc build can\'t be notarized', r.stderr)
        self.assertNothingLeft()

    def test_a_certificate_that_is_not_developer_id_application(self):
        self.certificate('A', FINGERPRINT, 'Apple Development: Example Maker (%s)' % TEAM)
        write(os.path.join(self.fake, 'identities'), '  1) %s "Apple Development: Example Maker (%s)"\n'
              % (FINGERPRINT, TEAM))
        r = self.release('--identity', FINGERPRINT)
        self.assertEqual(r.returncode, 69, r.stdout + r.stderr)
        self.assertIn('is not a Developer ID Application certificate', r.stderr)
        self.assertNothingLeft()

    def test_an_ended_certificate(self):
        self.mark('A.ends-within-0')
        r = self.release('--identity', FINGERPRINT)
        self.assertEqual(r.returncode, 69, r.stdout + r.stderr)
        self.assertIn('has ended (Feb  1 12:00:00 2028 GMT)', r.stderr)
        self.assertNothingLeft()

    # --- --check: every test, nothing built ---

    def test_check_builds_nothing(self):
        r = self.release('--identity', FINGERPRINT, '--notarize', 'example-profile', '--check')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.compiled(), self.signed()), ([], []))
        made = ('xcrun notarytool submit', 'xcrun stapler staple', 'lipo -create', 'spctl')
        self.assertFalse([c for c in calls(self.fake) if c.startswith(made)])
        self.assertNothingLeft()
        for words in ('identity       %s' % SIGNER, 'fingerprint    %s' % FINGERPRINT,
                      'issuer         /CN=Developer ID Certification Authority/OU=G2/O=Apple Inc./C=US '
                      '(Apple\'s G2 intermediate)', 'ends           Feb  1 12:00:00 2028 GMT', 'team           ' + TEAM,
                      'screen         unlocked', 'notary profile example-profile (accepted by notarytool history)',
                      'minimum macOS  12.0 (LSMinimumSystemVersion in router/Info.plist)',
                      'Everything a release build needs is here. Nothing was built.'):
            self.assertIn(words, r.stdout)

    def test_check_names_every_problem_at_once(self):
        os.remove(os.path.join(self.bin, 'lipo'))
        self.mark('locked')
        self.mark('A.ends-within-7776000')
        self.certificate('A', FINGERPRINT, SIGNER, g2=False, ends='Jan 10 12:00:00 2027 GMT')
        r = self.release('--identity', FINGERPRINT, '--notarize', 'example-profile', '--check')
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('lipo is missing', r.stdout)
        self.assertIn('The screen is locked', r.stdout)
        self.assertIn('ends           Jan 10 12:00:00 2027 GMT: within 90 days. Renew the certificate before '
                      'the next release.', r.stdout)
        self.assertIn('not Apple\'s G2 intermediate', r.stdout)
        self.assertIn('Not ready: 2 problems above. Nothing was built.', r.stdout)
        self.assertEqual(self.compiled(), [])
        self.assertNothingLeft()

    def test_two_certificates_with_one_name_are_told_apart_by_fingerprint(self):
        self.certificate('B', OTHER_FINGERPRINT, SIGNER, g2=False, ends='Mar  3 12:00:00 2021 GMT')
        write(os.path.join(self.fake, 'certificates'), self.pem('B', 'A'))
        write(os.path.join(self.fake, 'identities'), '  1) %s "%s"\n  2) %s "%s"\n'
              % (OTHER_FINGERPRINT, SIGNER, FINGERPRINT, SIGNER))
        r = self.release('--identity', FINGERPRINT.lower(), '--check')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('ends           Feb  1 12:00:00 2028 GMT', r.stdout)
        self.assertNotIn('2021', r.stdout)

    # --- the builds ---

    def test_an_ad_hoc_release_build(self):
        r = self.release('--identity', '-')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(os.listdir(self.out)), ['SwitchboardLinks.zip', 'links.json'])
        self.assertNothingLeft(out_too=False)
        compiled = self.compiled()
        self.assertEqual(len(compiled), 2, compiled)
        self.assertIn('-target arm64-apple-macos12.0', compiled[0])
        self.assertIn('-target x86_64-apple-macos12.0', compiled[1])
        for c in compiled:
            self.assertIn(' -O ', c)
            self.assertIn('-module-cache-path ', c)          # its module cache stays in the temporary folder
        self.assertEqual([c.split(' -output ')[0] for c in calls(self.fake) if c.startswith('lipo -create')],
                         ['lipo -create'])
        signs = self.signed()
        self.assertEqual(len(signs), 1, signs)
        self.assertTrue(signs[0].startswith('codesign --force --options runtime --sign - '), signs[0])
        self.assertTrue([c for c in calls(self.fake) if c.startswith('codesign --verify --strict --verbose=2 ')])
        self.assertFalse([c for c in calls(self.fake) if c.startswith(('xcrun', 'spctl', 'security'))])
        info = self.links_json()
        self.assertEqual(info, {'version': '0.2.0', 'build': '2', 'bundle_id': 'com.example.links-test',
                                'zip_sha256': sha256(os.path.join(self.out, 'SwitchboardLinks.zip')),
                                'source_sha256': sha256(os.path.join(self.router, 'SwitchboardLinks.swift')),
                                'team': '', 'signer': 'ad hoc', 'architectures': ['arm64', 'x86_64'],
                                'minimum_macos': '12.0', 'notarized': False})
        unpacked = os.path.join(self.tmp, 'unpacked')
        subprocess.run(['/usr/bin/ditto', '-x', '-k', os.path.join(self.out, 'SwitchboardLinks.zip'), unpacked],
                       check=True, timeout=60)
        self.assertEqual(files(unpacked), ['Switchboard Links.app', 'Switchboard Links.app/Contents',
                                           'Switchboard Links.app/Contents/Info.plist',
                                           'Switchboard Links.app/Contents/MacOS',
                                           'Switchboard Links.app/Contents/MacOS/SwitchboardLinks',
                                           'Switchboard Links.app/Contents/Resources',
                                           'Switchboard Links.app/Contents/Resources/SwitchboardLinks.icns'])
        with open(os.path.join(unpacked, 'Switchboard Links.app', 'Contents', 'Info.plist')) as f:
            self.assertEqual(f.read(), PLIST)                  # the same Info.plist, so the same bundle identifier
        self.assertIn('ad hoc', r.stdout)
        self.assertIn('the installer never uses an ad-hoc build', r.stdout)

    def test_a_signed_notarized_release_build(self):
        write(os.path.join(self.fake, 'display'), display(stapled=True))
        self.mark('A.ends-within-7776000')
        r = self.release('--identity', FINGERPRINT, '--notarize', 'example-profile')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(os.listdir(self.out)), ['SwitchboardLinks.zip', 'links.json'])
        self.assertNothingLeft(out_too=False)
        signs = self.signed()
        self.assertEqual(signs, ['codesign --force --options runtime --timestamp --sign %s %s'
                                 % (FINGERPRINT, signs[0].split(FINGERPRINT + ' ')[1])])
        order = [c.split()[0] + ' ' + c.split()[1] for c in calls(self.fake)
                 if c.startswith(('codesign --force', 'xcrun notarytool submit', 'xcrun stapler', 'spctl'))]
        self.assertEqual(order, ['codesign --force', 'xcrun notarytool', 'xcrun stapler', 'spctl --assess'])
        submit = next(c for c in calls(self.fake) if 'notarytool submit' in c)
        self.assertIn('--keychain-profile example-profile --wait', submit)
        info = self.links_json()
        self.assertEqual((info['team'], info['signer'], info['notarized']), (TEAM, SIGNER, True))
        self.assertEqual(info['zip_sha256'], sha256(os.path.join(self.out, 'SwitchboardLinks.zip')))
        unpacked = os.path.join(self.tmp, 'unpacked')
        subprocess.run(['/usr/bin/ditto', '-x', '-k', os.path.join(self.out, 'SwitchboardLinks.zip'), unpacked],
                       check=True, timeout=60)
        self.assertTrue(os.path.exists(os.path.join(unpacked, 'Switchboard Links.app', 'Contents', 'CodeResources')))
        # Before signing it says whose certificate it is, its issuer and its end date, and warns of the end.
        before = r.stdout[:r.stdout.index('Signed')]
        for words in ('identity       ' + SIGNER, 'Apple\'s G2 intermediate', 'ends           Feb  1 12:00:00 2028 '
                      'GMT: within 90 days.'):
            self.assertIn(words, before)
        self.assertIn('LINKS_TEAM="%s"' % TEAM, r.stdout)

    def test_a_refused_notarization_writes_nothing(self):
        write(os.path.join(self.fake, 'submit'), '  id: 00000000-0000-4000-8000-000000000002\n  status: Invalid\n')
        r = self.release('--identity', FINGERPRINT, '--notarize', 'example-profile')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('xcrun notarytool log 00000000-0000-4000-8000-000000000002 --keychain-profile example-profile',
                      r.stderr)
        self.assertNothingLeft()

    def test_a_failed_signature_writes_nothing(self):
        self.mark('sign-fails')
        r = self.release('--identity', FINGERPRINT)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('codesign could not sign the app', r.stderr)
        self.assertNothingLeft()


# ---------- the installer's link app block, run on its own ----------

def install_sh():
    with open(os.path.join(ROOT, 'install.sh')) as f:
        return f.read()


def where(text, marker, start=0):
    """Where `marker` is in install.sh's text. The installer tests lift parts of install.sh out by these markers, so a
    marker that moved or was renamed fails here with its name, not with a bare ValueError further on."""
    at = text.find(marker, start)
    if at < 0:
        raise AssertionError('install.sh no longer has %r: the link app tests lift its parts out by it' % marker)
    return at


def link_app_block(team, text=None):
    """install.sh's helpers and its link app block, as one script, with LINKS_TEAM set to `team`."""
    text = install_sh() if text is None else text
    start = where(text, '\nLINE_OPEN=0\n') + 1                  # say, note, begin, done_in, result, short, ...
    helpers = text[start:where(text, '\n', where(text, '\nLSREGISTER=') + 1) + 1]     # ... up to LSREGISTER
    at = where(text, '\napple_tool_real() {') + 1               # one line, or up to its closing brace
    end = text.index('\n', at) if text[at:text.index('\n', at)].rstrip().endswith('}') else where(text, '\n}\n', at) + 2
    tool = text[at:end]
    old = next(line for line in text.splitlines() if line.startswith('OLD_LABEL='))     # an older install's ids
    at = where(text, '# ---- the link app, Switchboard Links')
    block = text[at:where(text, '\n# ---- ', at) + 1]                  # up to the installer's next part
    if len(re.findall(r'(?m)^LINKS_TEAM="[A-Z0-9]*"$', block)) != 1:
        raise AssertionError('install.sh\'s link app block must have exactly one LINKS_TEAM="..." line')
    block = re.sub(r'(?m)^LINKS_TEAM="[A-Z0-9]*"$', 'LINKS_TEAM="%s"' % team, block)
    return ('set -eu\nDIM=""; BOLD=""; PLAIN=""\n' + old + '\n' + helpers + '\n' + tool + '\n'
            'DATA="$1"; PY="$2"; LINKAPP="$3"; MACHINE="$4"; LS=0\n' + block + 'echo "the block ended"\n')


class InstallerTakesThePrebuiltApp(unittest.TestCase):
    """The link app block of install.sh, with a release's prebuilt app in router/prebuilt."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-prebuilt-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.home, '.switchboard')
        self.router = os.path.join(self.data, 'app', 'router')
        self.prebuilt = os.path.join(self.router, 'prebuilt')
        self.fake = os.path.join(self.tmp, 'fake')
        self.temp = os.path.join(self.tmp, 'temp')
        self.bin = os.path.join(self.tmp, 'bin')
        for d in (self.prebuilt, self.fake, self.temp, self.bin):
            os.makedirs(d)
        shutil.copy(os.path.join(ROOT, 'router', 'build.sh'), self.router)
        write(os.path.join(self.router, 'Info.plist'), PLIST)
        write(os.path.join(self.router, 'SwitchboardLinks.swift'), SWIFT)
        for name, text in (('swiftc', SWIFTC), ('codesign', CODESIGN), ('xcode-select', XCODE_SELECT),
                           ('sw_vers', SW_VERS)):
            write(os.path.join(self.bin, name), text, 0o755)
        write(os.path.join(self.fake, 'compiled-mark'), 'compiled on this Mac')
        write(os.path.join(self.fake, 'display'), display())
        self.app = os.path.join(self.home, 'Applications', 'Switchboard Links.app')
        self.make_prebuilt()

    def make_prebuilt(self, **change):
        made = os.path.join(self.tmp, 'made', 'Switchboard Links.app')
        shutil.rmtree(os.path.dirname(made), True)
        write(os.path.join(made, 'Contents', 'Info.plist'), change.pop('plist', PLIST))
        write(os.path.join(made, 'Contents', 'MacOS', 'SwitchboardLinks'), '#!/bin/sh\n# the release\'s copy\n', 0o755)
        write(os.path.join(made, 'Contents', 'CodeResources'), 'a made-up ticket\n')
        zip_path = os.path.join(self.prebuilt, 'SwitchboardLinks.zip')
        if os.path.exists(zip_path):
            os.remove(zip_path)
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', made, zip_path], check=True, timeout=60)
        info = {'version': '0.2.0', 'build': '2', 'bundle_id': 'com.example.links-test', 'zip_sha256': sha256(zip_path),
                'source_sha256': hashlib.sha256(SWIFT.encode()).hexdigest(), 'team': TEAM, 'signer': SIGNER,
                'architectures': ['arm64', 'x86_64'], 'minimum_macos': '12.0', 'notarized': True}
        info.update(change)
        write(os.path.join(self.prebuilt, 'links.json'), json.dumps(info, indent=2) + '\n')

    def install(self, team=TEAM, linkapp=1, machine='arm64', **env):
        environment = dict(PATH=self.bin + ':/usr/bin:/bin:/usr/sbin:/sbin', HOME=self.home, TMPDIR=self.temp + '/',
                           FAKE=self.fake, LANG='en_US.UTF-8', **env)
        return subprocess.run(['/bin/sh', '-c', link_app_block(team), 'sh', self.data, sys.executable, str(linkapp),
                               machine], capture_output=True, text=True, env=environment, timeout=120)

    def program(self):
        with open(os.path.join(self.app, 'Contents', 'MacOS', 'SwitchboardLinks')) as f:
            return f.read()

    def compiled(self):
        return [c for c in calls(self.fake) if c.startswith('swiftc')]

    def assertTaken(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, 'Installed the link app from the release: signed by %s, checksum and signature '
                         'checked, with a stapled notarization ticket.\nthe block ended\n' % SIGNER)
        self.assertEqual(self.compiled(), [])                  # the compiler is never called
        self.assertEqual(self.program(), '#!/bin/sh\n# the release\'s copy\n')
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.app))), ['Switchboard Links.app'])
        self.assertEqual(os.listdir(self.temp), [])

    def assertBuiltHere(self, r, why):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], 'Not using the release\'s link app: %s.' % why, r.stdout)
        self.assertRegex(lines[1], r'^Building the link app… \d+ s$')
        self.assertEqual(len(lines), 3, r.stdout)
        self.assertEqual(len(self.compiled()), 1)
        self.assertEqual(self.program(), '#!/bin/sh\n# compiled on this Mac\n')     # never the prebuilt one
        self.assertEqual(os.listdir(self.temp), [])

    def test_a_sound_prebuilt_app_is_taken_without_the_compiler(self):
        r = self.install()
        self.assertTaken(r)
        self.assertTrue([c for c in calls(self.fake) if c.startswith('codesign --verify --strict ')])
        with open(os.path.join(self.app, 'Contents', 'Info.plist')) as f:
            self.assertEqual(f.read(), PLIST)

    def test_a_quarantined_download_leaves_no_quarantine_flag_on_the_app(self):
        # Homebrew marks what it downloads, and copies of it keep the mark: unpacked as it is, the app would carry it,
        # and macOS would ask before the first link opens. A compiled copy carries none, and this one is checked.
        subprocess.run(['/usr/bin/xattr', '-w', 'com.apple.quarantine', '0081;00000000;Example;',
                        os.path.join(self.prebuilt, 'SwitchboardLinks.zip')], check=True, timeout=30)
        self.assertTaken(self.install())
        for folder, dirs, names in os.walk(self.app):
            for name in [''] + dirs + names:
                path = os.path.join(folder, name)
                r = subprocess.run(['/usr/bin/xattr', '-p', 'com.apple.quarantine', path], capture_output=True,
                                   timeout=30)
                self.assertNotEqual(r.returncode, 0, path)

    def test_it_takes_the_place_of_an_older_copy_as_a_build_does(self):
        os.makedirs(os.path.join(self.app, 'Contents', 'MacOS'))
        write(os.path.join(self.app, 'Contents', 'Info.plist'), PLIST)
        write(os.path.join(self.app, 'Contents', 'MacOS', 'SwitchboardLinks'), '#!/bin/sh\n# older\n', 0o755)
        self.assertTaken(self.install())

    def test_a_wrong_checksum(self):
        self.make_prebuilt(zip_sha256='0' * 64)
        self.assertBuiltHere(self.install(), 'its SHA-256 does not match links.json')

    def test_a_bad_signature(self):
        self.mark('verify-fails')
        self.assertBuiltHere(self.install(), 'its signature did not verify (codesign --verify --strict)')

    def test_signed_by_another_team_than_links_json_names(self):
        write(os.path.join(self.fake, 'display'), display(team='ZZZZZ99999'))
        self.assertBuiltHere(self.install(), 'it is signed by team ZZZZZ99999, and links.json names %s' % TEAM)

    def test_a_team_this_install_sh_does_not_trust(self):
        self.assertBuiltHere(self.install(team='ZZZZZ99999'),
                             'it is signed by team %s, and this install.sh trusts only ZZZZZ99999' % TEAM)

    def test_an_empty_team_constant_never_uses_a_prebuilt_app(self):
        self.assertBuiltHere(self.install(team=''),
                             'this installer does not name the Developer ID team it trusts')
        self.assertFalse([c for c in calls(self.fake) if c.startswith('codesign --verify')])

    def test_an_older_macos(self):
        write(os.path.join(self.fake, 'macos'), '11.7.10\n')
        self.assertBuiltHere(self.install(), 'it needs macOS 12.0 or newer, and this Mac has 11.7.10')

    def test_a_newer_macos_with_a_two_digit_minor_version(self):
        write(os.path.join(self.fake, 'macos'), '12.10\n')
        self.make_prebuilt(minimum_macos='12.9')
        self.assertTaken(self.install())

    def test_a_damaged_zip(self):
        zip_path = os.path.join(self.prebuilt, 'SwitchboardLinks.zip')
        with open(zip_path, 'r+b') as f:
            f.seek(10)
            f.write(b'\0' * 200)
        with open(os.path.join(self.prebuilt, 'links.json')) as f:
            info = json.load(f)
        info['zip_sha256'] = sha256(zip_path)                  # the checksum matches, the zip is still damaged
        write(os.path.join(self.prebuilt, 'links.json'), json.dumps(info))
        self.assertBuiltHere(self.install(), 'its zip could not be unpacked')

    def test_links_json_missing_or_damaged(self):
        write(os.path.join(self.prebuilt, 'links.json'), '{"zip_sha256": ')
        self.assertBuiltHere(self.install(), 'its links.json is missing or can\'t be read')
        os.remove(os.path.join(self.prebuilt, 'links.json'))
        os.remove(os.path.join(self.fake, 'calls'))
        shutil.rmtree(self.app)
        self.assertBuiltHere(self.install(), 'its links.json is missing or can\'t be read')

    def test_built_from_another_source(self):
        self.make_prebuilt(source_sha256='1' * 64)
        self.assertBuiltHere(self.install(), 'it was built from another version of router/SwitchboardLinks.swift')

    def test_another_info_plist(self):
        self.make_prebuilt(plist=PLIST.replace('com.example.links-test', 'com.example.other'))
        self.assertBuiltHere(self.install(), 'its Info.plist is not the one in router/Info.plist')

    def test_not_notarized(self):
        self.make_prebuilt(notarized=False)
        self.assertBuiltHere(self.install(), 'links.json says it is not notarized')
        write(os.path.join(self.fake, 'display'), display(stapled=False))
        self.make_prebuilt()
        os.remove(os.path.join(self.fake, 'calls'))
        self.assertBuiltHere(self.install(), 'its signature carries no notarization ticket')

    def test_no_build_for_this_processor(self):
        self.make_prebuilt(architectures=['arm64'])
        self.assertBuiltHere(self.install(machine='x86_64'), 'it has no build for this Mac\'s processor (x86_64)')

    def test_without_the_command_line_tools_a_failed_check_skips_the_link_app(self):
        self.make_prebuilt(zip_sha256='0' * 64)
        os.remove(os.path.join(self.bin, 'xcode-select'))
        write(os.path.join(self.bin, 'xcode-select'), '#!/bin/sh\nexit 2\n', 0o755)
        r = self.install()
        self.assertEqual(r.stdout, 'Not using the release\'s link app: its SHA-256 does not match links.json.\n'
                         'Skipped the link app: it needs Apple\'s command line tools (xcode-select --install). '
                         'Everything else works without it.\nthe block ended\n')
        self.assertFalse(os.path.exists(self.app))

    def test_no_prebuilt_app_builds_as_before_and_says_nothing_more(self):
        shutil.rmtree(self.prebuilt)
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r'^Building the link app… \d+ s\nthe block ended\n$')
        self.assertEqual(len(self.compiled()), 1)

    def test_asked_to_build_it_here(self):
        r = self.install(SWITCHBOARD_BUILD_LINKS='1')
        self.assertRegex(r.stdout, r'^Building the link app… \d+ s\nthe block ended\n$')
        self.assertEqual(self.program(), '#!/bin/sh\n# compiled on this Mac\n')

    def test_no_link_app_touches_nothing(self):
        r = self.install(linkapp=0)
        self.assertEqual((r.returncode, r.stdout), (0, 'the block ended\n'), r.stderr)
        self.assertEqual(calls(self.fake), [])
        self.assertFalse(os.path.exists(os.path.join(self.home, 'Applications')))

    def test_the_block_sets_no_trap_and_the_shipped_constant_is_a_team_or_empty(self):
        text = install_sh()
        block = text[text.index('# ---- the link app, Switchboard Links'):text.index("# ---- the menu bar item's app")]
        self.assertNotIn('trap ', block)                       # a piped install's own trap cleans its download
        self.assertRegex(block, r'(?m)^LINKS_TEAM="([A-Z0-9]{10})?"$')

    def test_a_tree_that_carries_a_prebuilt_app_names_the_team_it_trusts(self):
        # A release with the zip and an empty LINKS_TEAM would show every installing person a line about a step the
        # release left out, and build the app anyway. The tree a release is cut from must not be in that state.
        if os.path.exists(os.path.join(ROOT, 'router', 'prebuilt', 'SwitchboardLinks.zip')):
            self.assertTrue(re.search(r'(?m)^LINKS_TEAM="[A-Z0-9]{10}"$', install_sh()),
                            'router/prebuilt/SwitchboardLinks.zip is in the tree, and install.sh\'s LINKS_TEAM is '
                            'empty: set it to the team router/build.sh --release printed')

    def test_a_marker_that_moved_is_named(self):
        text = install_sh()
        for marker in ('\nLINE_OPEN=0\n', '\nLSREGISTER=', '\napple_tool_real() {',
                       '# ---- the link app, Switchboard Links'):
            with self.assertRaisesRegex(AssertionError, re.escape(repr(marker))):
                link_app_block(TEAM, text.replace(marker, '\n# moved\n'))

    def test_the_block_ends_at_the_installers_next_part(self):
        # The link app's part ends where the next part starts, whichever it is: the one-time move of an older
        # install (it calls launchctl, which the test fence refuses) sits between it and the menu bar item's app.
        at = install_sh().index('# ---- the link app, Switchboard Links')
        nxt = install_sh().index('\n# ---- ', at) + 1
        block = link_app_block(TEAM)
        self.assertIn([x for x in install_sh()[at:nxt].splitlines() if x.strip()][-1], block)
        self.assertNotIn(install_sh()[nxt:install_sh().index('\n', nxt)], block)
        self.assertNotIn('launchctl', block)

    def test_a_helper_written_over_several_lines_is_lifted_whole(self):
        text = install_sh()
        line = next(x for x in text.splitlines() if x.startswith('apple_tool_real() {'))
        body = line[len('apple_tool_real() {'):-1].strip().rstrip(';')
        self.assertTaken(self.install_text(text.replace(line, 'apple_tool_real() {\n  %s\n}' % body)))

    def install_text(self, text):
        environment = dict(PATH=self.bin + ':/usr/bin:/bin:/usr/sbin:/sbin', HOME=self.home, TMPDIR=self.temp + '/',
                           FAKE=self.fake, LANG='en_US.UTF-8')
        return subprocess.run(['/bin/sh', '-c', link_app_block(TEAM, text), 'sh', self.data, sys.executable, '1',
                               'arm64'], capture_output=True, text=True, env=environment, timeout=120)

    def mark(self, name):
        write(os.path.join(self.fake, name), '')


class PlaceFromAPrebuiltApp(unittest.TestCase):
    """sh router/build.sh --from APP [--no-register] FOLDER: an app already built and signed takes the place of the
    one in FOLDER as a freshly built one does, and only after its signature verifies again."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-place-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fake = os.path.join(self.tmp, 'fake')
        self.bin = os.path.join(self.tmp, 'bin')
        self.temp = os.path.join(self.tmp, 'temp')
        for d in (self.fake, self.bin, self.temp):
            os.makedirs(d)
        write(os.path.join(self.bin, 'codesign'), CODESIGN, 0o755)
        write(os.path.join(self.bin, 'swiftc'), SWIFTC, 0o755)
        self.made = os.path.join(self.tmp, 'made', 'Switchboard Links.app')
        write(os.path.join(self.made, 'Contents', 'Info.plist'), PLIST)
        write(os.path.join(self.made, 'Contents', 'MacOS', 'SwitchboardLinks'), '#!/bin/sh\n# prebuilt\n', 0o755)
        self.dest = os.path.join(self.tmp, 'home', 'Applications')
        self.app = os.path.join(self.dest, 'Switchboard Links.app')

    def place(self):
        env = dict(PATH=self.bin + ':/usr/bin:/bin:/usr/sbin:/sbin', TMPDIR=self.temp + '/', FAKE=self.fake,
                   HOME=os.path.join(self.tmp, 'home'))
        return subprocess.run(['/bin/sh', os.path.join(ROOT, 'router', 'build.sh'), '--from', self.made,
                               '--no-register', self.dest], capture_output=True, text=True, env=env, timeout=60)

    def test_it_is_put_in_place_and_left_where_it_came_from(self):
        write(os.path.join(self.app, 'Contents', 'old'), 'older build\n')
        r = self.place()
        self.assertEqual((r.returncode, r.stdout), (0, 'put %s in place\n' % self.app), r.stderr)
        self.assertEqual(files(self.app), ['Contents', 'Contents/Info.plist', 'Contents/MacOS',
                                           'Contents/MacOS/SwitchboardLinks'])
        self.assertTrue(os.path.exists(self.made))
        self.assertEqual([c for c in calls(self.fake) if c.startswith('swiftc')], [])
        self.assertEqual(os.listdir(self.temp), [])

    def test_a_signature_that_does_not_verify_leaves_the_one_there(self):
        write(os.path.join(self.app, 'Contents', 'old'), 'the one that works\n')
        write(os.path.join(self.fake, 'verify-fails'), '')
        r = self.place()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(files(self.app), ['Contents', 'Contents/old'])
        self.assertEqual(os.listdir(self.temp), [])


if __name__ == '__main__':
    unittest.main()
