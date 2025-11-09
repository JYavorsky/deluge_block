import signal
import sys
import time
import configparser
import logging
from logging.handlers import RotatingFileHandler
from deluge_client import DelugeRPCClient


class DelugeWatchdogService:
    """Monitors Deluge for forbidden or unwanted files, removes torrents if necessary,
    and gracefully handles reconnects and Docker shutdown signals."""

    def __init__(self, config_path="/app/config.ini"):
        self.config = self.load_config(config_path)
        self.logger = self.setup_logging()
        self.client = None
        self.running = True
        self.dcache = []

        # Register signal handlers for Docker stop/restart
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    # ----------------------------------------------------------
    # Configuration & Logging
    # ----------------------------------------------------------
    def load_config(self, path):
        config = configparser.ConfigParser()
        read_files = config.read(path)
        if not read_files:
            print(f"[FATAL] Config file not found: {path}")
            sys.exit(1)
        for section in ("login", "lists"):
            if section not in config:
                print(f"[FATAL] Missing [{section}] section in config.ini")
                sys.exit(1)
        return config

    def setup_logging(self):
        logger = logging.getLogger("DelugeWatchdog")
        # Avoid adding duplicate handlers if logger already configured
        if logger.handlers:
            return logger
        
        logger.setLevel(logging.DEBUG)

        # Level from config
        log_level_str = self.config.get("logging", "level").upper()
        level = getattr(logging, log_level_str, logging.INFO)

        # Console handler (for Docker logs)
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
        logger.addHandler(ch)

        # Optional rotating file handler
        if self.config.getboolean("logging", "enabled"):
            log_file = self.config.get("logging", "file")
            max_bytes = self.config.getint("logging", "max_bytes")
            backup_count = self.config.getint("logging", "backup_count")

            fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
            fh.setLevel(level)
            fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
            logger.addHandler(fh)
            logger.info(
                f"File logging enabled -> {log_file} (max {max_bytes/1024/1024:.1f} MB x {backup_count})"
            )
        else:
            logger.info("File logging disabled; console output only.")
        return logger

    # ----------------------------------------------------------
    # Connection Management
    # ----------------------------------------------------------
    def connect_to_deluge(self, retries=5, delay=10):
        """Attempt to connect to the Deluge daemon with retries."""
        for attempt in range(1, retries + 1):
            try:
                self.client = DelugeRPCClient(
                    self.config["login"]["host"],
                    int(self.config["login"]["port"]),
                    self.config["login"]["user"],
                    self.config["login"]["password"],
                )
                self.client.connect()
                self.logger.info("Connected to Deluge daemon.")
                return True
            except Exception as e:
                self.logger.error(f"Connection attempt {attempt}/{retries} failed: {e}")
                if attempt < retries:
                    self.logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    self.logger.critical(
                        "Max connection retries reached. Exiting to trigger Docker restart."
                    )
                    sys.exit(1)

    def check_connection(self):
        """Verify Deluge connection, reconnect if needed."""
        try:
            self.client.call("daemon.info")
            version = info.get(b"version", b"Unknown Version").decode("utf-8", errors="ignore")
            self.logger.info(f"Connected to Deluge daemon (version: {version})")
            return True
        except Exception:
            self.logger.warning("Connection lost. Attempting to reconnect...")
            return self.connect_to_deluge()

    # ----------------------------------------------------------
    # Signal Handling
    # ----------------------------------------------------------
    def signal_handler(self, sig, frame):
        """Graceful shutdown signal handler."""
        self.logger.info(f"Received signal {sig}, shutting down gracefully...")
        self.running = False

    # ----------------------------------------------------------
    # Torrent Processing
    # ----------------------------------------------------------
    def check_and_remove_torrents(self, forbidden_extensions, unwanted_extensions):
        """Scans torrents and removes/adjusts based on file rules."""
        try:
            torrents = self.client.call(
                "core.get_torrents_status", {}, [b"name", b"files", b"file_priorities"]
            )
        except Exception as e:
            self.logger.error(f"Failed to fetch torrent list: {e}")
            return

        for torrent_id, torrent_data in torrents.items():
            torrent_name = torrent_data.get(b"name", b"Unknown Name").decode("utf-8", errors="ignore")

            if torrent_name in self.dcache:
                continue
            self.dcache.append(torrent_name)

            self.logger.info(f"Validating '{torrent_name}'")

            files = torrent_data.get(b"files", [])
            file_priorities = torrent_data.get(b"file_priorities", [])

            if not files:
                self.logger.warning(f"No files found for '{torrent_name}', skipping.")
                continue

            priorities = []
            forbidden = False
            forbidden_filename = ""

            for file_info, file_priority in zip(files, file_priorities):
                file_name = file_info.get(b"path", b"").decode("utf-8", errors="ignore")

                # Adjust unwanted files
                if file_priority > 0:
                    if any(file_name.endswith(ext) for ext in unwanted_extensions):
                        self.logger.info(f" -> Skipping unwanted file: {file_name}")
                        priorities.append(0)
                    else:
                        priorities.append(file_priority)
                else:
                    priorities.append(file_priority)

                # Detect forbidden files
                if any(file_name.endswith(ext) for ext in forbidden_extensions):
                    forbidden = True
                    forbidden_filename = file_name
                    break

            # Take action
            try:
                if forbidden:
                    self.logger.warning(
                        f"Removing '{torrent_name}' due to forbidden file '{forbidden_filename}'"
                    )
                    self.client.call(
                        "core.remove_torrent", torrent_id.decode("utf-8", errors="ignore"), True
                    )
                else:
                    self.client.call(
                        "core.set_torrent_options",
                        torrent_id.decode("utf-8", errors="ignore"),
                        {"file_priorities": priorities},
                    )
            except Exception as e:
                self.logger.error(f"Error processing '{torrent_name}': {e}")

    # ----------------------------------------------------------
    # Main Loop
    # ----------------------------------------------------------
    def run(self):
        """Main monitoring loop."""
        forbidden_extensions = [
            ext.strip() for ext in self.config["lists"]["forbidden"].split(",") if ext.strip()
        ]
        unwanted_extensions = [
            ext.strip() for ext in self.config["lists"]["unwanted"].split(",") if ext.strip()
        ]

        self.logger.info("Starting Deluge check service...")
        self.logger.info(f"Loaded {len(forbidden_extensions)} forbidden extensions")
        self.logger.info(f"Loaded {len(unwanted_extensions)} unwanted extensions")

        # Read timing values from config.ini
        check_interval = self.config.getint("timing", "torrent_check_interval")
        connection_interval = self.config.getint("timing", "connection_check_interval")

        # Initial connect with retries (exits on failure)
        if not self.connect_to_deluge():
            self.logger.error("Unable to connect to Deluge daemon. Exiting.")
            sys.exit(1)

        last_connection_check = time.time()

        while self.running:
            try:
                self.logger.info(f"Checking torrents @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
                self.check_and_remove_torrents(forbidden_extensions, unwanted_extensions)
            except Exception as e:
                # Catch-all to prevent the service dying silently
                self.logger.exception(f"Unhandled error during torrent check: {e}")

            # Check connection every 5 minutes
            if time.time() - last_connection_check >= 300:
                self.logger.info("Performing scheduled connection health check")
                self.check_connection()
                last_connection_check = time.time()

            # Sleep 60 seconds, exit early if stopped
            for _ in range(60):
                if not self.running:
                    break
                time.sleep(1)

        self.logger.info("Deluge check service stopped cleanly.")


# ----------------------------------------------------------
# Entry Point
# ----------------------------------------------------------
if __name__ == "__main__":
    service = DelugeWatchdogService()
    service.run()
    
