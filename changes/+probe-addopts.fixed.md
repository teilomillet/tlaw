`tlaw check` and `tlaw mutate` no longer apply the target project's pytest `addopts`, so probing a project that configures a plugin tlaw does not autoload no longer fails as a pytest usage error.
