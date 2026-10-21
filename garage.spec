# PyInstaller-configuratie voor Garage.exe (map-variant: start sneller dan één los bestand).
# Bouwen: bouwen.bat
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ["Garage.pyw"],
    pathex=["."],
    datas=[("app/templates", "app/templates"), ("app/static", "app/static"),
           ("app/ocr.ps1", "app"), ("app/melding.ps1", "app")]
          + collect_data_files("reportlab") + collect_data_files("webview"),
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("app") + ["clr"],
    excludes=["tests", "pytest", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Garage",
    icon="app/static/garage.ico",
    console=False,
    version="tools/versie.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, name="Garage")
