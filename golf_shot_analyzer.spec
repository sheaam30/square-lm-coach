# golf_shot_analyzer.spec
# Reproducible PyInstaller spec for Golf Shot Analyzer (square-lm-coach)
#
# Build locally (Windows):
#   pip install pyinstaller requests
#   pyinstaller golf_shot_analyzer.spec
#
# Output: dist/square-lm-coach.exe

from PyInstaller.utils.hooks import collect_all

# Collect requests + certifi so SSL works inside the frozen exe.
# Without collect_all('certifi'), cacert.pem is missing and HTTPS calls fail.
requests_datas, requests_binaries, requests_hiddenimports = collect_all('requests')
certifi_datas,  certifi_binaries,  certifi_hiddenimports  = collect_all('certifi')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=requests_binaries + certifi_binaries,
    datas=requests_datas + certifi_datas,
    hiddenimports=(
        requests_hiddenimports
        + certifi_hiddenimports
        + [
            'tkinter',
            'tkinter.ttk',
            'tkinter.scrolledtext',
            'tkinter.filedialog',
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim unused stdlib heavyweights to reduce exe size.
        # NOTE: do NOT exclude 'email' — urllib3 (inside requests) requires it.
        'unittest',
        'html',
        'http.server',
        'xmlrpc',
        'pydoc',
        'doctest',
        'difflib',
        'ftplib',
        'imaplib',
        'poplib',
        'smtplib',
        'telnetlib',
        'nntplib',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='square-lm-coach',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # UPX is pre-installed on windows-latest CI runner
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # no terminal window — pure GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # add an .ico path here if you want a custom icon
)
