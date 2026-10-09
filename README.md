# Floret

**A run-to-completion kernel for small embedded systems.**

Wait for the event, and do only what's necessary to process it.

A daisy's flower head is not one flower: it is hundreds of tiny florets working together as one. A Floret application is built the same way: a handful of small tasks, each woken by events, each running to completion, sharing one stack and one CPU, with no context switches. Priority is simply the order of the task table. When nothing is happening, the CPU sleeps.

Floret is written in plain C11, has no dependencies, and is meant to be easy to read in an afternoon.

> **Status:** design stage. See [docs/design.md](docs/design.md) for the kernel and [docs/flight_test.md](docs/flight_test.md) for **Flight Test**, the system that lets you watch a running Floret application: the on-target telemetry block, the **Downlink** host tool, and the **uplink** command word.

## License

MIT, see [LICENSE](LICENSE).
