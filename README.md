# icloud-s3-python

A fake S3 server that hides your files inside PNG photos sized for iCloud Shared Albums. Python port of [ashayas/icloud-s3](https://github.com/ashayas/icloud-s3).

The joke: Shared Albums didn't count against iCloud storage. An Apple account gets 200 of them with 5,000 photos each, and a 2048×2048 PNG holds about 12 MB, so in theory that's about 12 TB for 0 B of quota.

> [!WARNING]
> It's a joke, and the loophole is closing. Since iOS 27 and macOS 27, new Shared Albums keep full resolution and [count against the owner's iCloud storage](https://support.apple.com/en-us/108916). Only albums made on older software still don't. There are no passwords and no auth, and if Apple ever recompresses a photo, the file is gone. Don't put anything real in it.

## Run it

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). It fetches Python 3.14 for you.

```sh
git clone https://github.com/7331/icloud-s3-python && cd icloud-s3-python
uv run icloud-s3
```

In a second terminal, store a file and get it back with the AWS CLI. The keys can be anything:

```sh
export AWS_ACCESS_KEY_ID=anything AWS_SECRET_ACCESS_KEY=anything AWS_DEFAULT_REGION=us-east-1
alias aws='uvx --from awscli aws --endpoint-url http://127.0.0.1:9311'

aws s3 mb s3://my-bucket
aws s3 cp notes.txt s3://my-bucket/notes.txt
aws s3 cp s3://my-bucket/notes.txt notes-back.txt
curl -o photo.png 'http://127.0.0.1:9311/my-bucket/notes.txt?format=png&chunk=0'
```

The last line saves the photo your file became.

## Settings

Set them as env vars or in a `.env` file:

| Variable | Default | What it does |
|---|---|---|
| `ICLOUD_S3_ALBUM_DIRECTORY` | unset, so in memory | Keep the PNGs here, and rebuild everything from them on restart |
| `ICLOUD_S3_SHARD_COUNT` | `1` | Shared Albums per bucket, up to 200 |
| `ICLOUD_S3_IS_PHOTOS_APP_SYNC_ENABLED` | `false` | On a Mac, also import each PNG into Photos |
| `ICLOUD_S3_MAXIMUM_CHUNK_SIZE_BYTES` | `2097152` (2 MiB) | Bytes per photo, up to 12,181,504. The 12 TB figure needs the maximum |
| `ICLOUD_S3_PORT` | `9311` | Port to listen on |
| `ICLOUD_S3_HOST` | `127.0.0.1` | Address to listen on |

```sh
ICLOUD_S3_ALBUM_DIRECTORY=./albums uv run icloud-s3
```

To get files back from photos you saved, put them in `./albums/icloud-s3-anything/` and start the server with that directory. It reads every file's bucket and key out of the pixels.

## Use it as a folder

[rclone](https://rclone.org/) can mount a bucket as a normal folder. Anything you copy in becomes photos:

```sh
rclone config create icloud s3 provider=Other endpoint=http://127.0.0.1:9311 access_key_id=x secret_access_key=x
mkdir ~/icloud-s3
rclone mount icloud:my-bucket ~/icloud-s3 --vfs-cache-mode writes --vfs-write-back 0s
```

On a Mac, use `rclone nfsmount` instead of `rclone mount` so you don't need macFUSE. Subfolders work, but on disk and in Photos each bucket stays one flat album.

## What works

Tested with the AWS CLI, boto3, rclone, s3cmd, s5cmd, MinIO `mc` and restic (yes, real encrypted backups into photos). Point any of them at `http://127.0.0.1:9311` with path-style addressing.

- Buckets: create, list, delete
- Files: upload, download, partial download (`Range`), copy, move, delete, metadata
- Big files: multipart upload
- Listing: folders (`--delimiter`), prefixes, more than 1,000 keys

## How it works

Every file is cut into chunks of up to 2 MiB, and each chunk becomes one PNG. Its bytes go into the pixel colours, 3 per pixel, under a banner showing the file name. Each chunk has a CRC32 and each file a SHA-256, so a single changed pixel is caught. The PNG format matches the Rust original byte for byte, so either one can read the other's photos.

## Development

```sh
uv run ruff format --check . && uv run ruff check . && uv run ty check && uv run lint-imports && uv run deptry . && uv run vulture && uv run xenon --max-absolute C --max-modules B --max-average A icloud_s3
```
