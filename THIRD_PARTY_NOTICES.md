# Third-party notices

RWS Stress Tester's own code is licensed under the MIT License (see [`LICENSE`](LICENSE)). The MIT
License does **not** apply to the third-party software listed below. Each item keeps its own
license, and you must follow those terms when you use or redistribute this package.

The bundled engines are shipped unchanged, except that debugging symbols (`.pdb`) are left out of
the package and y-cruncher's folder includes Microsoft Visual C++ runtime DLLs (see below).

---

## Bundled engines (`Engines\`)

| Software | Version | Copyright | License | License file |
|---|---|---|---|---|
| [y-cruncher](http://www.numberworld.org/y-cruncher/) | 0.8.7 Build 9547 | © 2008-2025 Alexander J. Yee | y-cruncher license, free for non-commercial use | `Engines/y-cruncher/Read Me.txt` |
| [FurMark 2](https://www.geeks3d.com/furmark/) | 2.0.17.0 | © 2007-2025 Geeks3D | FurMark 2 EULA, freeware | `Engines/FurMark/EULA.txt` |
| [DiskSpd](https://github.com/microsoft/diskspd) | 2.2.0 | © Microsoft Corporation | Microsoft Software License Terms | `Engines/DiskSpd/EULA` |
| [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) | 0.9.6 | © LibreHardwareMonitor contributors | MPL-2.0 | [`licenses/LibreHardwareMonitor-MPL-2.0.txt`](licenses/LibreHardwareMonitor-MPL-2.0.txt) |
| [PawnIO](https://pawnio.eu/) driver installer | 2.2.0 | © namazso | GPL-2.0 or later | [`licenses/PawnIO-GPL-2.0.txt`](licenses/PawnIO-GPL-2.0.txt) |
| Microsoft Visual C++ runtime (`msvcp140.dll`, `vcruntime140.dll`, 32-bit) | 14.44 | © Microsoft Corporation | Microsoft Visual C++ Redistributable license | — |

### y-cruncher
y-cruncher may be mirrored and distributed free of charge, and used free of charge for any
**non-commercial** purpose (or any academic purpose, including technology reviews). **Commercial
use requires permission from the developer**, Alexander J. Yee (a-yee@u.northwestern.edu). The
current license is at <http://www.numberworld.org/y-cruncher/license.html>.

### FurMark 2
FurMark 2 is freeware by Geeks3D, free for private and commercial use. It may not be sold or
rented, modified, or repackaged in a new installer, and its notice must not be removed. Its
homepage is <https://www.geeks3d.com/furmark/>. Distribution other than through websites is subject
to the author's permission (jegx@geeks3d.com).

### DiskSpd
The DiskSpd binaries are Microsoft's official release (`DiskSpd.zip`, v2.2) and come with the
Microsoft Software License Terms in `Engines/DiskSpd/EULA`. The DiskSpd source code is published
under the MIT License at <https://github.com/microsoft/diskspd>.

### LibreHardwareMonitor
LibreHardwareMonitor is covered by the Mozilla Public License 2.0. Its source code is available at
<https://github.com/LibreHardwareMonitor/LibreHardwareMonitor> (release `v0.9.6`). The libraries
that come with it in the same folder (`HidSharp`, `OxyPlot`, `Aga.Controls`, `DiskInfoToolkit`,
`RAMSPDToolkit`, `BlackSharp.Core`, `Microsoft.Win32.TaskScheduler` and the `System.*` /
`Microsoft.Bcl.*` assemblies) are distributed as part of that release under their own licenses.

### PawnIO
`Engines/PawnIO_Driver/PawnIO_setup.exe` installs the PawnIO driver, which LibreHardwareMonitor
uses to read CPU sensors. PawnIO is licensed under the GNU General Public License, version 2 or
later. Its source code is available at <https://github.com/namazso/PawnIO>, with the installer at
<https://github.com/namazso/PawnIO.Setup>.

---

## Bundled in `RWS_StressTester.exe`

PyInstaller packs the Python runtime and these libraries into the executable. Their license texts
are in [`licenses/`](licenses/).

| Software | License | License file |
|---|---|---|
| [Python](https://www.python.org/) 3.12 | PSF License | [`licenses/Python-PSF.txt`](licenses/Python-PSF.txt) |
| [Tcl/Tk](https://www.tcl-lang.org/) (used by tkinter) | Tcl/Tk License (BSD-style) | [`licenses/Tcl-Tk.txt`](licenses/Tcl-Tk.txt) |
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) | MIT | [`licenses/python-packages/customtkinter.txt`](licenses/python-packages/customtkinter.txt) |
| [darkdetect](https://github.com/albertosottile/darkdetect) | BSD-3-Clause | [`licenses/python-packages/darkdetect.txt`](licenses/python-packages/darkdetect.txt) |
| [packaging](https://github.com/pypa/packaging) | Apache-2.0 or BSD-2-Clause | [`licenses/python-packages/packaging.txt`](licenses/python-packages/packaging.txt) |
| [Pillow](https://python-pillow.org/) | MIT-CMU | [`licenses/python-packages/pillow.txt`](licenses/python-packages/pillow.txt) |
| [psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause | [`licenses/python-packages/psutil.txt`](licenses/python-packages/psutil.txt) |
| [pythonnet](https://github.com/pythonnet/pythonnet) | MIT | [`licenses/python-packages/pythonnet.txt`](licenses/python-packages/pythonnet.txt) |
| [clr-loader](https://github.com/pythonnet/clr-loader) | MIT | [`licenses/python-packages/clr_loader.txt`](licenses/python-packages/clr_loader.txt) |
| [cffi](https://github.com/python-cffi/cffi) | MIT-0 | [`licenses/python-packages/cffi.txt`](licenses/python-packages/cffi.txt) |
| [pycparser](https://github.com/eliben/pycparser) | BSD-3-Clause | [`licenses/python-packages/pycparser.txt`](licenses/python-packages/pycparser.txt) |
| [ReportLab](https://www.reportlab.com/) | BSD | [`licenses/python-packages/reportlab.txt`](licenses/python-packages/reportlab.txt) |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) | MIT | [`licenses/python-packages/charset-normalizer.txt`](licenses/python-packages/charset-normalizer.txt) |

`requirements.txt` does not pin versions. When a dependency is added or its license changes,
update this file and `licenses/`.
