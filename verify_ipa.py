"""Reject missing, corrupt or simulator artifacts before offering an IPA."""
import hashlib
import json
import plistlib
import sys
import zipfile
from pathlib import Path

ipa = Path(sys.argv[1])
with zipfile.ZipFile(ipa) as archive:
    assert archive.testzip() is None, 'Corrupt IPA archive'
    candidates = [name for name in archive.namelist()
                  if name.startswith('Payload/') and name.count('/') == 2 and name.endswith('.app/Info.plist')]
    assert len(candidates) == 1, f'Expected one main application: {candidates}'
    path = candidates[0]
    info = plistlib.loads(archive.read(path))
    assert info['CFBundleIdentifier'] == 'me.lunagram.ios', info['CFBundleIdentifier']
    assert info['CFBundleDisplayName'] == 'LunaGram'
    assert info['DTPlatformName'] == 'iphoneos', 'Not an iPhone device build'
    executable = path.removesuffix('Info.plist') + info['CFBundleExecutable']
    binary = archive.read(executable)
    assert binary[:4] == b'\xcf\xfa\xed\xfe', 'Expected a 64-bit Mach-O binary'
    assert int.from_bytes(binary[4:8], 'little') == 0x100000c, 'Expected ARM64'
    summary = {'name': 'LunaGram', 'bundle_id': info['CFBundleIdentifier'],
        'version': info['CFBundleShortVersionString'], 'build': info['CFBundleVersion'],
        'platform': info['DTPlatformName'], 'sha256': hashlib.sha256(ipa.read_bytes()).hexdigest(),
        'signing': 'Unsigned; sign with your own certificate in ESign before installing',
        'source': 'https://github.com/iamxvbaba/gramsrv-Telegram-iOS',
        'source_revision': '16f191d403e3b587578f8f32bae814cce1681853',
        'runtime_tests': 'Not yet tested on an iPhone'}
    ipa.with_suffix('.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))
