print-config-claude.json is the snippet `codegraph install --print-config claude` prints (codegraph v1.6.2,
src/installer/targets/claude.ts printConfig: JSON.stringify({ mcpServers: { codegraph: getClaudeMcpServerConfig() } },
null, 2)). Switchboard writes the same entry with the command as a full path. Refresh it from the real program when
the pin moves; this copy was checked against the downloaded v1.6.2 release.
