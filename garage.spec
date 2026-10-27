# PyInstaller configuration for Garage.exe (folder variant: starts faster than a single file).
# Build: build.bat
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ["Garage.pyw"],
    pathex=["."],
    datas=[("app/templates", "app/templates"), ("app/static", "app/static"),
           ("app/ocr.ps1", "app"), ("app/notification.ps1", "app")]
          + collect_data_files("reportlab") + collect_data_files("webview"),
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("app") + ["clr"],
    excludes=["tests", "pytest", "tkinter"],
)
# Pillow parts Garage does not use (AVIF, FreeType fonts, color profiles, Tk): saves about 10 MB.
# Pillow handles their absence itself; Garage only reads JPG/PNG and only draws rectangles.
PIL_UNUSED = {"_avif", "_imagingft", "_imagingcms", "_imagingtk"}
a.binaries = [b for b in a.binaries if not (b[0].replace("\\", "/").startswith("PIL/")
                                             and b[0].replace("\\", "/")[4:].split(".")[0] in PIL_UNUSED)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Garage",
    icon="app/static/garage.ico",
    console=False,
    version="tools/version.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, name="Garage")
