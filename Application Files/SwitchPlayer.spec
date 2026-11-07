import os
import re

with open(os.path.join(SPECPATH, 'main.py'), encoding='utf-8') as f:
    VERSION = re.search(r'^VERSION = "([^"]+)"', f.read(), re.MULTILINE).group(1)

a = Analysis(
    ['main.py'],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        ('index.html', '.'),
        ('Brewfile', '.'),
        ('nxnative-python/pyproject.toml', 'nxnative-python'),
        ('../THIRD-PARTY-NOTICES', '.'),
    ],
    hiddenimports=[
        'webview',
        'webview.platforms.cocoa',
        'objc',
        'Foundation',
        'AppKit',
        'WebKit',
        'PyObjCTools.AppHelper',
        'Quartz',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'webview.platforms.android',
        'webview.platforms.cef',
        'webview.platforms.edgechromium',
        'webview.platforms.gtk',
        'webview.platforms.mshtml',
        'webview.platforms.qt',
        'webview.platforms.winforms',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Switch Player',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    target_arch='arm64',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Switch Player',
)

app = BUNDLE(
    coll,
    name='Switch Player.app',
    icon=os.path.join(SPECPATH, 'SwitchPlayer.icns'),  # from SwitchPlayer.svg: scripts/make_icon.py
    bundle_identifier='com.digitaln.switchplayer',
    info_plist={
        'CFBundleName': 'Switch Player',
        'CFBundleDisplayName': 'Switch Player',
        'CFBundleVersion': VERSION,
        'CFBundleShortVersionString': VERSION,
        'LSMinimumSystemVersion': '26.6',
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'See THIRD-PARTY-NOTICES.',
    },
)
