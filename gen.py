import requests
import hashlib
import jinja2
from glob import glob
import json
import base64
import sys
import tarfile
import subprocess
import toml
from io import BytesIO
from joblib import Memory
memory = Memory("~/.cache/python_cache", verbose=0)


base_url = "https://crates.io/"

def crate_url(name, version = None):
    ret =  "https://crates.io/api/v1/crates/" + name
    if version is not None:
        ret += "/" + version

    return ret

@memory.cache
def get(url, *args, **kwargs):
    return requests.get(url, *args, **kwargs)

@memory.cache
def local_manifests():
    manifests = {}

    if len(sys.argv) >= 4:
        path = sys.argv[3][:-10]
        for cargo_toml in glob(path + "**" + "/Cargo.toml", recursive=True):
            cargo_toml = toml.load(cargo_toml)
            
            if "package" in cargo_toml:
                manifests[cargo_toml["package"]["name"]] = cargo_toml

    return manifests

@memory.cache
def crate_json(name, version = None):
    # prioritize local crates if we have a local lock file

    if name in local_manifests():
        cargo_toml = local_manifests()[name]
            
        if "package" in cargo_toml:
            if cargo_toml["package"]["name"] == name:
                if version is None or cargo_toml["package"]["version"] == version:
                    if "license" in cargo_toml["package"]:
                        license = cargo_toml["package"]["license"]
                    else:
                        license = None
                    
                    if "homepage" in cargo_toml["package"]:
                        homepage = cargo_toml["package"]["homepage"]
                    elif "repository" in cargo_toml["package"]:
                        homepage = cargo_toml["package"]["repository"]
                    else:
                        homepage = "FILLMEIN"

                    if "description" in cargo_toml["package"]:
                        desc = cargo_toml["package"]["description"]
                    else:
                        desc = ""

                    ret = { "crate" : 
                           { "description" : desc, 
                             "homepage": homepage }, 
                            "version":               
                           { "license" : license } } 

                    return ret

    # ok, nothing local matches, just go to crates.io
    return get(crate_url(name, version)).json()

@memory.cache
def crate_max_version(name):
    return crate_json(name)["crate"]["max_version"]

@memory.cache
def crate_download(name, version):
    try:
        dl_path = crate_json(name, version)["version"]["dl_path"]

        while True:
            try:
                return get(base_url + dl_path, stream=True)
            except:
                continue
    except:
        return None



@memory.cache
def crate_hash(name, version):
    content = crate_download(name, version)
    if content:
        m = hashlib.sha256()
        m.update(content.content)
        return nix_base32(m.digest())
    else:
        return "FILLMEIN"

def crate_dependencies(name, version, deps = None):
    crate_meta = crate_json(name, version)

    if "version" in crate_meta and "links" in crate_meta["version"]:
        deps = get(base_url + crate_meta["version"]["links"]["dependencies"]).json()
        
        return { dep["crate_id"] : dep for dep in deps["dependencies"] }
    else:
        assert deps is not None, "crate not found on crates.io and package is None"

        return { name : { "kind": "normal" } for name, version in deps }

def nix_base32(h):
    chars = "0123456789abcdfghijklmnpqrsvwxyz"
    l = int((len(h) * 8 - 1) / 5) + 1

    s = ""

    for n in range(l - 1, -1, -1):
        b = n * 5
        i = int(b / 8)
        j = b % 8

        c = h[i] >> j

        if i < (len(h) - 1):
            c |= h[i + 1] << (8 - j)

        s += chars[c & 0x1f]

    return s


name = sys.argv[1]

if len(sys.argv) == 2:
    version = crate_max_version(name)
elif len(sys.argv) == 3:
    version = sys.argv[2]


if len(sys.argv) == 4:
    version = sys.argv[2]
    lockfile = toml.load(sys.argv[3])
else:
    tar = crate_download(name, version).content
    tar = BytesIO(tar)
    tar = tarfile.open(fileobj=tar)
    tar.extractall("crates_downloads/")
    
    crate_dir = "crates_downloads/" + name + "-" + version
    
    subprocess.run(["cargo", "generate-lockfile"], cwd=crate_dir)
    
    lockfile = toml.load(crate_dir + "/Cargo.lock")

def guix_name(name, version):
    return name + "_" + version.replace(".", "_") 

def gen_package(package, packages):
    print(package, file=sys.stderr)

    if "dependencies" in package:
        real_deps = [real_dep.split()[:2] if len(real_dep.split()) >= 2 else [real_dep, [p["version"] for p in packages if p["name"] == real_dep][0]] for real_dep in package["dependencies"]]
    else: 
        real_deps = []

    deps = crate_dependencies(package["name"], package["version"], real_deps)

    normal = ["normal", "build"]
    
    normal_deps = [ d for d in real_deps if deps[d[0]]["kind"] in normal]
    dev_deps = [ d for d in real_deps if deps[d[0]]["kind"] not in normal]

    desc = crate_json(package["name"])["crate"]["description"]
    homepage = crate_json(package["name"])["crate"]["homepage"]
    license = crate_json(package["name"], package["version"])["version"]["license"]

    if homepage is not None:
        homepage = homepage.strip()

    if desc is not None:
        desc = desc.strip()

    if license is None:
        license = '#f'
    else:
        licenses = license.split('/')
        if len(licenses) == 1:
            license = "(spdx-string->license \"{}\")".format(license)
        else:
            license = "`("

            for l in licenses[:-1]:
                license += "(spdx-string->license \"{}\")\n               ".format(l)

            license += "(spdx-string->license \"{}\"))".format(licenses[-1])

    t = \
r"""(define-public rust-{{guix_name(package["name"], package["version"])}}
  (package
    (name "rust-{{package["name"]}}")
    (version "{{package["version"]}}")
    (source
      (origin
        (method url-fetch)
        (uri (crate-uri "{{package["name"]}}" version))
        (file-name
          (string-append name "-" version ".tar.gz"))
        (sha256
          (base32
            "{{crate_hash(package["name"], package["version"])}}"))))
    (build-system cargo-build-system)
    {% if normal_deps|length + dev_deps|length > 0 %}
    (arguments
    `({% if normal_deps|length > 0 %}
#:cargo-inputs
      ({% for dep in normal_deps %}{% if not loop.first %}       {% endif %}("rust-{{dep[0]}}" ,rust-{{guix_name(dep[0], dep[1])}}){% if not loop.last %}
        
      {% endif %}{% endfor %}){% endif %}
    {% if dev_deps|length > 0 %}
{% if normal_deps|length > 0 %}{{"\n      "}}{% else %}{% endif %}#:cargo-development-inputs
      ({% for dep in dev_deps %}{% if not loop.first %}       {% endif %}("rust-{{dep[0]}}" ,rust-{{guix_name(dep[0], dep[1])}}){% if not loop.last %}
        
      {% endif %}{% endfor %}){% endif %}))
    {% endif %}
    (home-page "{{homepage|default("")}}")
    (synopsis {{desc|tojson}})
    (description
      (beautify-description {{desc|tojson}}))
    (license {{license}})))
"""

    compiled = jinja2.Template(t, trim_blocks=True, lstrip_blocks=True)

    return compiled.render({**globals(), **locals()})


header = \
"""(use-modules (guix build-system cargo))
(use-modules (guix licenses))
(use-modules (guix packages))
(use-modules (guix download))
(use-modules ((guix import utils) 
             #:select (beautify-description spdx-string->license)))
"""

print(header)

from multiprocessing import Pool

def gen(x):
    return gen_package(*x)

with Pool(processes=24) as pool:
    for p in pool.map(gen, zip(lockfile["package"], [lockfile["package"]] * len(lockfile["package"]))):
        print(p)
        print()

print()
print("rust-" + name + "_" + version.replace(".", "_"))


# TODO(robin): rust-time-0.1.42 is broken???
# also rand-0.7.0 (because it has rand_hc as dev and as normal dependency)
