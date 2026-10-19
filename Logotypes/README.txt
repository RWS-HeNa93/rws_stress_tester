RWS Stress Tester
Certificate Logo Folder

Purpose
Place image files here when you want to brand the generated System Stability Certificate.

Supported formats:
- PNG
- JPG
- JPEG

How this folder is used in the app:
1. Launch RWS_StressTester.exe.
2. On the setup screen, go to the Stability Certificate section.
3. Use the Browse button next to the Logo field.
4. Select an image from this Logotypes folder.
5. Optionally enable Customer Name and Machine Name fields.
6. Start the diagnostics run.

What happens next:
- The selected logo path is saved in settings.json.
- If report generation is enabled, the chosen image is placed at the top of the PDF certificate.
- Generated PDF reports are written to the reports\ folder.

Notes:
- The app opens this Logotypes folder as the default browsing location when it exists.
- If the selected logo file is moved or deleted later, the app can still generate the PDF without a logo image.
- This README only covers the logo folder. See the packaged root README.txt for the full app workflow and UI.

Packaging
This folder, including this README, is copied into the packaged distribution by build.bat.
