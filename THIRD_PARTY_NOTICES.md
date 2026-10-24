# Third-party notices

Python: PSF license, runtime/LICENSE.txt.
RapidOCR and bundled PaddleOCR-derived weights: Apache-2.0, see runtime/Lib/site-packages/rapidocr*dist-info and MODEL_LICENSES.md.
scrcpy: Apache-2.0, tools/scrcpy-win64-v5.0.1/LICENSE.txt. ADB notices: third_party/licenses/ADB-NOTICE.txt.
FFmpeg 9.0.2: LGPL-2.1-or-later, shared DLLs, no GPL/nonfree configure options enabled in the upstream Windows build.
libusb 1.0.30: LGPL-2.1-or-later, shared DLL. dav1d 1.5.4: BSD-2-Clause. SDL 3.4.18: zlib.
Corresponding source archives, full license texts and unmodified scrcpy dependency build scripts are included under third_party/. DLL replacement and reverse engineering for debugging modifications of LGPL components are not prohibited by this project.
Other Python wheel components, including OpenCV/FFmpeg and Shapely/GEOS, retain their bundled license notices under runtime/Lib/site-packages and third_party/licenses; versions are listed in DEPENDENCIES.json.
No Microsoft fonts are redistributed; the app uses a locally installed Windows font when available.
Project-authored source has not been assigned an open-source license.

GEOS 3.14.1: LGPL-2.1-or-later; corresponding source is included in third_party/sources. The optional OpenCV video-file FFmpeg DLL is omitted; image decoding remains enabled.
