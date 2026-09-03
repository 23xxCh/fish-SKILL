import multiprocessing

multiprocessing.freeze_support()

from goofish_collector.app import main


raise SystemExit(main())
