import logging
import os
import sys
import json
from datetime import datetime


LOG_DIR = "logs"
LOG_FILE_FORMAT = "agent_%Y-%m-%d.log"
CONSOLE_LEVEL = logging.INFO   
FILE_LEVEL = logging.DEBUG    

class Color:
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

class ColoredFormatter(logging.Formatter):
    
    FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
    
    FORMATS = {
        logging.DEBUG:    Color.CYAN + FORMAT + Color.RESET,
        logging.INFO:     Color.GREEN + FORMAT + Color.RESET,
        logging.WARNING:  Color.YELLOW + FORMAT + Color.RESET,
        logging.ERROR:    Color.RED + FORMAT + Color.RESET,
        logging.CRITICAL: Color.RED + "\033[1m" + FORMAT + Color.RESET,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt="%H:%M:%S")
        return formatter.format(record)

def setup_logger(name="CodeReviewer"):
    
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    filename = datetime.now().strftime(LOG_FILE_FORMAT)
    filepath = os.path.join(LOG_DIR, filename)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    file_handler = logging.FileHandler(filepath, encoding='utf-8')
    file_handler.setLevel(FILE_LEVEL)
    file_formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(CONSOLE_LEVEL)
    console_handler.setFormatter(ColoredFormatter())
    logger.addHandler(console_handler)

    return logger

log = setup_logger()

def log_json(title, data, level=logging.INFO):
    try:
        if isinstance(data, str):
            data = json.loads(data)
        
        pretty_json = json.dumps(data, indent=2, ensure_ascii=False)
        log.log(level, f"{title}:\n{pretty_json}")
    except Exception:
        log.log(level, f"{title} (Raw):\n{data}")