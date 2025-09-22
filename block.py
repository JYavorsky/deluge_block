from deluge_client import DelugeRPCClient
import time
import configparser

import sys
import signal
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)  # send logs to stdout
    ]
)

log = logging.getLogger(__name__)

running = True

def handle_sigterm(signum, frame):
    global running
    log.info("Received SIGTERM, shutting down...")
    running = False

signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

config = configparser.ConfigParser()
config.read("/app/config.ini")

# Connect to Deluge
client = DelugeRPCClient(
    config["login"]["host"],
    int(config["login"]["port"]),
    config["login"]["user"],
    config["login"]["password"],
)

# Specify the forbidden file extensions
forbidden_extensions = config["lists"]["forbidden"].split(",")
unwanted_extensions = config["lists"]["unwanted"].split(",")

print("Scanning..")
print(" -> %i forbidden extensions" % len(forbidden_extensions))
print(" -> %i unwanted extensions" % len(unwanted_extensions))

dCache = []  # Cache of torrents we've evaluated..

# Function to check for forbidden files and remove the torrent
def check_and_remove_torrents():
    torrents = client.call(
        "core.get_torrents_status", {}, [b"name", b"files", b"file_priorities"]
    )

    for torrent_id, torrent_data in torrents.items():
        # Decode byte keys
        torrent_name = torrent_data.get(b"name", b"Unknown Name").decode(
            "utf-8", errors="ignore"
        )
        if torrent_name in dCache:
            continue
        else:
            dCache.append(torrent_name)
        files = torrent_data.get(b"files", [])
        file_priorities = torrent_data.get(b"file_priorities", [])
        print(f"Validating {torrent_name}")

        # If no files are listed, skip this torrent
        if not files:
            print(
                f"No files found for torrent '{torrent_name}' (ID: {torrent_id}), skipping."
            )
            continue

        # Check each file in the torrent for unwanted extensions
        priorities = []
        forbidden = False
        forbidden_filename = ""
        for file_info, file_priority in zip(files, file_priorities):
            file_name = file_info.get(b"path", b"").decode("utf-8", errors="ignore")
            if file_priority > 0:
                if any(file_name.endswith(ext) for ext in unwanted_extensions):
                    print(f"-> Skipping {file_name}")
                    priorities.append(0)  # skip
                else:
                    priorities.append(file_priority)
            else:
                priorities.append(file_priority)
                # Remove the torrent
            if any(file_name.endswith(ext) for ext in forbidden_extensions):
                # mark this, and abort further checks
                forbidden_filename = file_name
                forbidden = True
                break

        if forbidden:
            print(
                f"Removing torrent '{torrent_name}' due to forbidden file '{forbidden_filename}'"
            )
            # Remove the torrent
            client.call(
                "core.remove_torrent",
                torrent_id.decode("utf-8", errors="ignore"),
                True,
            )
        else:
            client.call(
                "core.set_torrent_options",
                torrent_id.decode("utf-8", errors="ignore"),
                {"file_priorities": priorities},
            )

# Run the function
while running:
    print("==> Beginning check [%s]..." % time.strftime("%Y-%m-%d %H:%M:%S"))
    check_and_remove_torrents()
    time.sleep(60)
    
log.info("Exited cleanly")
sys.exit(0)
