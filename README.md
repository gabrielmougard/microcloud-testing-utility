# Microcloud testing utility

This project contains useful scripts to iterate faster in the MicroCloud development process.

## `microcloud-side-load.py`

This script is used to setup a MicroCloud infrastructure (from scratch or from a local cache of VM backups)
or just simply refresh an existing infrastructure with the latest version of the MicroCloud snap.

After the scrit is run, you'll have 3 running VMs where you can run `microcloud init` from one of them (or use a preseed file)
and a stopped VM. Once MicroCloud is initialized, you can start the stopped VM and run `microcloud add` from a clustered VM to
test a scale-up scenario.

```bash
# If you have no existing MicroCloud infrastructure, you can initialize it with:
./microcloud-side-load.py

# If you already have a MicroCloud infrastructure, but you want to purge it and reinitialize it, you can use:
./microcloud-side-load.py --init

# If you already have a cache folder populated with VM backups but you want to clear it and reinitialize it, you can use:
./microcloud-side-load.py <--init / ""> --purge-cache

# You can also specify a custom path for the cache directory:
./microcloud-side-load.py --cache /path/to/cache/dir

```