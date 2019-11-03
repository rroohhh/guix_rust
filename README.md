# guix_rust
Recursively create guix packages for rust crates from a lockfile or crates.io

Usage:
Either specify a crate available on crates.io and it will automatically create guix packages for the newest version. for example
```
python3.7 -u gen.py diesel
```
or specify a name, a version and a path to a lockfile, for example
```
python3.7 -u gen.py alacritty 0.3.3 ../alacritty/Cargo.lock 
```
