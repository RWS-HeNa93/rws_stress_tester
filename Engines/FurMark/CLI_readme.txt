Geeks3D FurMark v2.0.17.0
============================================
(c)2007-2024 Geeks3D
https://www.geeks3d.com/furmark/
Usage: furmark [--option [value]] ...

Options:
--help				print all options
--version			show version
--check-new-version		check for new version
--hpgfx value			set the high performance graphics state
				(0 or 1). For laptops with hybrid
				graphics (iGPU + dGPU)
--width value			set the width in pixels of the 3D window
--height value			set the height in pixels of the
				3D window
--fullscreen			run the demo or graphics test in
				fullscreen
--msaa value			set the number of samples for
				MSAA: 1, 2, 4 or 8
--demolist			print the list of available demos
--demo name			run a demo from its name.
				Use --demolist for all available names
--demofile filename		load and run a GeeXLab demo from a
				filename
--glinfo			print a quick OpenGL report
--glinfo-all			print a detailed OpenGL report
--glvendor			print the GL_VENDOR value
--glrenderer			print the GL_RENDERER value
--glversion			print the GL_VERSION value
--vkinfo			print a quick Vulkan report
--vkinfo-all			print a detailed Vulkan report
--vkinfo-extension 	info about a Vulkan extension.
				Should be used with --gpu-index
--gpuinfo			print GPU info
--glnvarchinfo			print information about NVIDIA GPU
				architecture
--gpu-index index		set the GPU index (0-based)
				for Vulkan/OpenCL/CUDA demos or info
--print-render-speed		print speed rendering info: frames,
				framerate, time step
--no-osi			disable the OSI (On Screen Information)
--no-resize			disable the resizing of the 3D window
--vsync number			set the VSYNC (vertical synchronization) state
--disable-demo-options		disable the display of the demo options
--p1080				benchmark preset:1080 (1920x1080)
--p1440				benchmark preset:1440 (2560x1440)
--p2160				benchmark preset:2160 (3840x2160)
--benchmark			benchmark the demo and returns to the
				shell at the end of the test unless
				the score box is displayed
--duration-ms ms		set the benchmark duration in milliseconds (only with custom settings, not in presets). Default: 60000
--max-time sec			set the max duration of the demo / stress test in seconds. Default: 0 (no limit)
--max-frames			set the max duration of the demo / stress test in frames. Default: 0 (no limit)
--no-score-box			disable the score box at the end
				of the benchmark
--no-gpumon			disable the GPU monitoring
--load-plugin-gpumon value	enable (1) or disable (0) the
				loading of the GPU monitoring plugin. Default: 1
--log-gpu-data			log GPU data into a CSV file
--gpu-monitor-print		print GPU monitoring info
--hw-polling-interval ms	set the GPU monitoring polling interval
				in milliseconds (default: 1000)
--disable-traces		disable traces (no writing in the
				log file)
--logfile-suffix suffix		add a suffix to the log file name
--glmemory-print		print OpenGL memory stats
				(NVIDIA OpenGL demos only)
--export-dir dir		set the export folder for GPU data
--artifact-scanner		run the artifact scannner
--artifact-scanner-interval	set the scanning interval in milliseconds
				(default: 100)
--furmark-bkg-img-id number	set the image for the dynanic background.
				Values: 0 to 3 - default is 0
--furmark-fur-img-id number	set the image for the fur color.
				Values: 0 to 1 - default is 0
--furmark-dxt5			use compressed textures (DXT5) in FurMark stress test
				(OpenGL only). Only in custom settings
--furmark-vram-test-gb		set the size in GB used by FurMark data.
				Values: 0, 2, 4, 6, 8, 12, 16, 20 or 24.
				Only valid in custom settings
--thread-affinity-mask mask	set the thread affinity mask.
				Default is 0 (= affinity mask not used)
--thread-core core_index	set the core (from 1 to n) on which
				the demo should run. Default
				is 0 (= core number not used)
--no-dpi-awareness		disable DPI awareness on Windows platform
--no-dpi-scaling		disable DPI scaling on Windows platform
--gpuz-print			print GPU-Z sensors in the terminal
--gpuz-log-data			log GPU-Z data into a CSV file