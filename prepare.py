"""Apply the LunaGram configuration to the pinned, public iOS source checkout."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE_REVISION = '16f191d403e3b587578f8f32bae814cce1681853'
BUNDLE_ID = 'me.lunagram.ios'

def replace(path, old, new, expected=1):
    content = path.read_text(encoding='utf-8')
    if content.count(old) != expected:
        raise RuntimeError(f'Unexpected source at {path}: {old!r}')
    path.write_text(content.replace(old, new), encoding='utf-8', newline='\n')

def prepare(root, assets):
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != SOURCE_REVISION:
        raise RuntimeError(f'Unexpected source revision: {revision}')
    replace(root/'submodules/TelegramCore/Sources/Network/Network.swift',
            '2: ["10.172.61.102"]', '2: ["31.77.146.26"]')
    replace(root/'submodules/TelegramCore/Sources/Network/Network.swift', 'port: 2398', 'port: 2408')
    key = (assets/'lunagram-public.pem').read_text().strip()
    if not key.startswith('-----BEGIN RSA PUBLIC KEY-----') or 'PRIVATE' in key:
        raise RuntimeError('Expected an RSA public key, never a private key')
    path = root/'submodules/MtProtoKit/Sources/MTDatacenterAuthMessageService.m'
    source = path.read_text(encoding='utf-8')
    literal = '@' + '\n             '.join('"'+line+'\\n"' for line in key.splitlines()[:-1])
    literal += '\n             "-----END RSA PUBLIC KEY-----"'
    source, count = re.subn(r'@"-----BEGIN RSA PUBLIC KEY-----\\n".*?"-----END RSA PUBLIC KEY-----"',
                            lambda _: literal, source, flags=re.S)
    if count != 2:
        raise RuntimeError(f'Expected two server trust lists, found {count}')
    path.write_text(source, encoding='utf-8', newline='\n')

    # Sideloading profiles may not grant App Groups. Keep main-app data inside
    # its own sandbox in that case; never replace a previously used container.
    delegate = root/'submodules/TelegramUI/Sources/AppDelegate.swift'
    container = '''private let lunagramAppContainer: (url: URL?, sharedIdentifier: String?) = {
    let manager = FileManager.default
    let standaloneUrl = manager.urls(for: .applicationSupportDirectory, in: .userDomainMask)
        .first?.appendingPathComponent("LunaGramContainer", isDirectory: true)
    if let standaloneUrl = standaloneUrl, manager.fileExists(atPath: standaloneUrl.path) {
        return (standaloneUrl, nil)
    }
    if let bundleId = Bundle.main.bundleIdentifier {
        let group = "group." + bundleId
        if let sharedUrl = manager.containerURL(forSecurityApplicationGroupIdentifier: group) {
            return (sharedUrl, group)
        }
    }
    guard let standaloneUrl = standaloneUrl else {
        return (nil, nil)
    }
    do {
        try manager.createDirectory(at: standaloneUrl, withIntermediateDirectories: true, attributes: nil)
        return (standaloneUrl, nil)
    } catch {
        return (nil, nil)
    }
}()

'''
    replace(delegate, 'private let handleVoipNotifications = false',
        container + 'private let handleVoipNotifications = false')
    replace(delegate,
        'let maybeAppGroupUrl = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroupName)',
        'let maybeAppGroupUrl = lunagramAppContainer.url')
    replace(delegate, 'configuration.sharedContainerIdentifier = appGroupName',
        'configuration.sharedContainerIdentifier = lunagramAppContainer.sharedIdentifier')

    configuration = dict(bundle_id=BUNDLE_ID, api_id='2040', api_hash='0'*32,
        team_id='LUNAGRAM00', app_center_id='0', is_internal_build='false',
        is_appstore_build='false', appstore_id='0', app_specific_url_scheme='lunagram',
        premium_iap_product_id='', enable_siri=False, enable_icloud=False)
    (root/'build-system/lunagram-configuration.json').write_text(json.dumps(configuration, indent=2)+'\n')

    # The output intentionally has no installable signature. ESign signs the app and
    # extensions with the user's own certificate after the build has completed.
    with (root/'.bazelrc').open('a', encoding='utf-8') as file:
        file.write('\n# LunaGram IPA for external signing\n'
            'build --//Telegram:disableProvisioningProfiles\n'
            'build --features=disable_legacy_signing\n'
            'build --worker_max_instances=SwiftCompile=1\n'
            'build --jobs=2\n')

    # Device bundle rules otherwise require a profile even when signing is
    # explicitly disabled. Keep the requirement for every normally signed build.
    replace(root/'build-system/bazel-rules/rules_apple/apple/internal/ios_rules.bzl',
        '    if platform_prerequisites.platform.is_device:\n'
        '        processor_partials.append(\n'
        '            partials.provisioning_profile_partial(',
        '    if platform_prerequisites.platform.is_device and "disable_legacy_signing" not in features:\n'
        '        processor_partials.append(\n'
        '            partials.provisioning_profile_partial(', expected=6)

    build = root/'Telegram/BUILD'
    source = build.read_text(encoding='utf-8')
    source, count = re.subn(r'(<key>CFBundleDisplayName</key>\s*)<string>Telegram</string>',
                            r'\1<string>LunaGram</string>', source)
    if count != 2:
        raise RuntimeError(f'Unexpected display-name declarations: {count}')
    old = 'app_icons = [ ":{}_icon".format(name) for name in composer_icon_folders ],'
    if source.count(old) != 1:
        raise RuntimeError('Unexpected default app icon declaration')
    source = source.replace(old, 'app_icons = [":LunaGramIcon"],')
    source += '\nfilegroup(\n    name = "LunaGramIcon",\n    srcs = glob(["LunaGram.xcassets/**"]),\n)\n'
    build.write_text(source, encoding='utf-8', newline='\n')
    for path in (root/'Telegram/Telegram-iOS').glob('*.lproj/InfoPlist.strings'):
        content = path.read_text(encoding='utf-8-sig')
        content = re.sub(r'("?CFBundleDisplayName"?\s*=\s*)"[^"]*"', r'\1"LunaGram"', content)
        path.write_text(content, encoding='utf-8', newline='\n')

    icon = root/'Telegram/LunaGram.xcassets/AppIcon.appiconset'
    icon.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(assets/'LunaGram.png', icon/'LunaGram.png')
    subprocess.run(['sips', '-z', '1024', '1024', str(icon/'LunaGram.png')], check=True)
    (icon/'Contents.json').write_text(json.dumps({
        'images': [{'filename':'LunaGram.png', 'idiom':'universal', 'platform':'ios', 'size':'1024x1024'}],
        'info': {'author':'xcode', 'version':1}}, indent=2))
    (icon.parent/'Contents.json').write_text('{"info":{"author":"xcode","version":1}}\n')
    print('Prepared LunaGram, DC2 31.77.146.26:2408, with an unsigned device build.')

if __name__ == '__main__':
    prepare(Path(sys.argv[1]).resolve(), Path(__file__).resolve().parent)
