# Requirements
pip install deluge-client


# To run the script

First edit the config-sample.ini file and save it as config.ini
Then just run block.ini


# What it does

Torrents that contain files with extensions in the "Forbidden" list will automatically be _removed_ from deluge.  This is to stop viruses or other unwanted content.

Torrents that contain files in the "unwanted" list will have those specific files flagged to Skip, but the torrent will be left alone.  This is to disable unwanted artwork, text files, etc that may come along with a torrent.

The script will check deluge every 60s to apply changes.

# THanks

Thanks to https://github.com/dcquence/deluge_exclude_files for getting this started!
