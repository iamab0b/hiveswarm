# Example adapter plugin

```
pip install -e examples/adapter-plugin
```

then in `~/.hiveswarm/worker.toml`:

```toml
[agents.example]
adapter = "example"
binary = "example-agent"
```

Restart the worker; it logs `adapter plugins: example`. See [docs/adapters.md](../../docs/adapters.md) for the contract.
