Usage: diskspd.exe [options] target1 [ target2 [ target3 ...] ]
version 2.2.0 (2024/6/3)

Valid targets:
     file_path
     #<physical drive number>
     <drive_letter>:

Sizes, offsets and lengths are specified as integer bytes, or with an
optional suffix of KMGT (KiB/MiB/GiB/TiB) or b (for blocks, see -b).
Examples: 4k = 4096
          with -b4k, 8b = 32768 (8 * 4KiB)

Available options:
  -?                    display usage information
  -:<flags>             experimental behaviors, as a bitmask of flags. current:
                          1 - allow throughput rate limit sleeps >1ms if indicated by rate
  -ag                   group affinity - threads assigned round-robin to CPUs by processor groups, 0 - n.
                          Groups are filled from lowest to highest processor before moving to the next.
                          [default; use -n to disable default affinity]
  -a[g#,]#[,#,...]>     advanced CPU affinity -  threads assigned round-robin to the CPUs stated, in order of
                          specification; g# is the processor group for the following CPUs. If no group is
                          stated, 0 is default. Additional groups/processors can be added, comma separated,
                          on the same or separate -a parameters.
                          Examples: -a0,1,2 and -ag0,0,1,2 are equivalent.
                                    -ag0,0,1,2,g1,0,1,2 specifies the first three CPUs in groups 0 and 1.
                                    -ag0,0,1,2,g1,0,1,2 and -ag0,0,1,2 -ag1,0,1,2 are equivalent.
  -b<size>              IO size, defines the block 'b' for sizes stated in units of blocks [default=64K]
  -B<base>[:length]     bounds; specify range of target to issue IO to - base offset and length
                          (default: IO is issued across the entire target)
  -c<size>              create file targets of the given size. Conflicts with non-file target specifications.
  -C<seconds>           cool down time - duration of the test after measurements finished [default=0s].
  -D<milliseconds>      Capture IOPs statistics in intervals of <milliseconds>; these are per-thread
                          per-target: text output provides IOPs standard deviation, XML provides the full
                          IOPs time series in addition. [default=1000, 1 second].
  -d<seconds>           duration (in seconds) to run test [default=10s]
  -f<size>              maximum target offset to issue IO to (non-inclusive); -Bbase -f(base+length) is the same
                         as -Bbase:length. For example, to test only the first sectors of a disk.
  -f<rst>               open file with one or more additional access hints
                          r : the FILE_FLAG_RANDOM_ACCESS hint
                          s : the FILE_FLAG_SEQUENTIAL_SCAN hint
                          t : the FILE_ATTRIBUTE_TEMPORARY hint
                          [default: none]
  -F<count>             total number of threads (conflicts with -t)
  -g<value>[i]          throughput per-thread per-target throttled to given value; defaults to bytes per millisecond
                          With the optional i qualifier the value is IOPS of the specified block size (-b).
                          Throughput limits cannot be specified when using completion routines (-x)
                          [default: no limit]
  -h                    deprecated, see -Sh
  -i<count>             number of IOs per burst; see -j [default: inactive]
  -j<milliseconds>      interval in <milliseconds> between issuing IO bursts; see -i [default: inactive]
  -I<priority>          Set IO priority to <priority>. Available values are: 1-very low, 2-low, 3-normal (default)
  -l                    Use large pages for IO buffers
  -L                    measure latency statistics
  -n                    disable default affinity (-a)
  -N<vni>               specify the flush mode for memory mapped I/O
                          v : uses the FlushViewOfFile API
                          n : uses the RtlFlushNonVolatileMemory API
                          i : uses RtlFlushNonVolatileMemory without waiting for the flush to drain
                          [default: none]
  -o<count>             number of outstanding I/O requests per target per thread
                          (1=synchronous I/O, unless more than 1 thread is specified with -F)
                          [default=2]
  -O<count>             number of outstanding I/O requests per thread - for use with -F
                          (1=synchronous I/O)
  -p                    start parallel sequential I/O operations with the same offset
                          (ignored if -r is specified, makes sense only with -o2 or greater)
  -P<count>             enable printing a progress dot after each <count> [default=65536]
                          completed I/O operations, counted separately by each thread
  -r[align]             random I/O aligned to [align] byte offsets within the target range (overrides -s)
                          [default alignment=block size (-b)]
  -rd<dist>[params]     specify an non-uniform distribution for random IO in the target
                          [default uniformly random]
                           distributions: pct, abs
                           all:  IO% and %Target/Size are cumulative. If the sum of IO% is less than 100% the
                                 remainder is applied to the remainder of the target. An IO% of 0 indicates a gap -
                                 no IO will be issued to that range of the target.
                           pct : parameter is a combination of IO%/%Target separated by : (colon)
                                 Example: -rdpct90/10:0/10:5/20 specifies 90% of IO in 10% of the target, no IO
                                   next 10%, 5% IO in the next 20% and the remaining 5% of IO in the last 60%
                           abs : parameter is a combination of IO%/Target Size separated by : (colon)
                                 If the actual target size is smaller than the distribution, the relative values of IO%
                                 for the valid elements define the effective distribution.
                                 Example: -rdabs90/10G:0/10G:5/20G specifies 90% of IO in 10GiB of the target, no IO
                                   next 10GiB, 5% IO in the next 20GiB and the remaining 5% of IO in the remaining
                                   capacity of the target. If the target is only 20G, the distribution truncates at
                                   90/10G:0:10G and all IO is directed to the first 10G (equivalent to -f10G).
  -rs<percentage>       percentage of requests which should be issued randomly; -r is used to specify IO alignment.
                          Sequential IO runs are homogeneous when a mixed r/w ratio is specified (-w) and their lengths
                          follow a geometric distribution based on the percentage (chance of next IO being sequential).
  -R[p]<text|xml>       output format. With the p prefix, the input profile (command line or XML) is validated and
                          re-output in the specified format without running load, useful for checking or building
                          complex profiles.
                          [default: text]
  -s[i][align]          stride size of [align] bytes, alignment & offset between operations
                          [default=non-interlocked, default alignment=block size (-b)]
                          By default threads track independent sequential IO offsets starting at base offset of the target.
                          With multiple threads this results in threads overlapping their IOs - see -T to divide
                          them into multiple separate sequential streams on the target.
                          With the optional i qualifier (-si) threads interlock on a shared sequential offset.
                          Interlocked operations may introduce overhead but make it possible to issue a single
                          sequential stream to a target which responds faster than one thread can drive.
                          (ignored if -r specified, -si conflicts with -p, -rs and -T)
  -S[bhmruw]            control caching behavior [default: caching is enabled, no writethrough]
                          non-conflicting flags may be combined in any order; ex: -Sbw, -Suw, -Swu
  -S                    equivalent to -Su
  -Sb                   enable caching (default, explicitly stated)
  -Sh                   equivalent -Suw
  -Sm                   enable memory mapped I/O
  -Su                   disable software caching, equivalent to FILE_FLAG_NO_BUFFERING
  -Sr                   disable local caching, with remote sw caching enabled; only valid for remote filesystems
  -Sw                   enable writethrough (no hardware write caching), equivalent to FILE_FLAG_WRITE_THROUGH or
                          non-temporal writes for memory mapped I/O (-Sm)
  -t<count>             number of threads per target (conflicts with -F)
  -T<offs>              starting separation between I/O operations performed on the same target by different threads
                          [default=0] (starting offset = base target offset + (thread number * <offs>)
                          only applies to -s sequential IO with #threads > 1, conflicts with -r and -si
  -v[s]                 verbose mode - with s, only provide additional summary statistics
  -w<percentage>        percentage of write requests (-w and -w0 are equivalent and result in a read-only workload).
                        absence of this switch indicates 100% reads
                          IMPORTANT: a write test will destroy existing data without a warning
  -W<seconds>           warm up time - duration of the test before measurements start [default=5s]
  -x                    use completion routines instead of I/O Completion Ports
  -X<filepath>          use an XML file to configure the workload. Profile defaults for -W/d/C (durations) and -R/v/z
                          (output format, verbosity and random seed) may be overriden by direct specification.
                          Targets can be defined in XML profiles as template paths of the form *<integer> (*1, *2, ...).
                          When run, specify the paths to substitute for the template paths in order on the command line.
                          The first specified target is *1, second is *2, and so on.
                          Example: diskspd -d60 -Xprof.xml first.bin second.bin (prof.xml using *1 and *2, 60s run)
  -z[seed]              set random seed [with no -z, seed=0; with plain -z, seed is based on system run time]

Write buffers:
  -Z                    zero buffers used for write tests
  -Zr                   per IO random buffers used for write tests - this incurrs additional run-time
                         overhead to create random content and shouln't be compared to results run
                         without -Zr
  -Z<size>              use a <size> buffer filled with random data as a source for write operations.
  -Z<size>,<file>       use a <size> buffer filled with data from <file> as a source for write operations.

  By default, write source buffers are filled with a repeating pattern (0, 1, 2, ..., 255, 0, 1, ...)

Synchronization:
  -ys<eventname>     signals event <eventname> before starting the actual run (no warmup)
                       (creates a notification event if <eventname> does not exist)
  -yf<eventname>     signals event <eventname> after the actual run finishes (no cooldown)
                       (creates a notification event if <eventname> does not exist)
  -yr<eventname>     waits on event <eventname> before starting the run (including warmup)
                       (creates a notification event if <eventname> does not exist)
  -yp<eventname>     stops the run when event <eventname> is set; CTRL+C is bound to this event
                       (creates a notification event if <eventname> does not exist)
  -ye<eventname>     sets event <eventname> and quits

Event Tracing:
  -e<q|c|s>             Use query perf timer (qpc), cycle count, or system timer respectively.
                          [default = q, query perf timer (qpc)]
  -ep                   use paged memory for the NT Kernel Logger [default=non-paged memory]
  -ePROCESS             process start & end
  -eTHREAD              thread start & end
  -eIMAGE_LOAD          image load
  -eDISK_IO             physical disk IO
  -eMEMORY_PAGE_FAULTS  all page faults
  -eMEMORY_HARD_FAULTS  hard faults only
  -eNETWORK             TCP/IP, UDP/IP send & receive
  -eREGISTRY            registry calls


Examples:

Create 8192KB file and run read test on it for 1 second:

  diskspd.exe -c8192K -d1 testfile.dat

Set block size to 4KB, create 2 threads per file, 32 overlapped (outstanding)
I/O operations per thread, disable all caching mechanisms and run block-aligned random
access read test lasting 10 seconds:

  diskspd.exe -b4K -t2 -r -o32 -d10 -Sh testfile.dat

Create two 1GB files, set block size to 4KB, create 2 threads per file, affinitize threads
to CPUs 0 and 1 (each file will have threads affinitized to both CPUs) and run read test
lasting 10 seconds:

  diskspd.exe -c1G -b4K -t2 -d10 -a0,1 testfile1.dat testfile2.dat