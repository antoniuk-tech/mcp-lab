# Defence checklist

Target: 10-15 minutes. Two terminal tabs, both in `~/dev/mcp-lab` with the
virtual environment active. Close every other window.

## Before starting

    source .venv/bin/activate
    wc -c .env                 # must be non-zero: both keys present
    git status --short         # must be empty: nothing uncommitted
    curl -s "https://api.openweathermap.org/data/2.5/weather?q=Kyiv&appid=$(grep OWM_API_KEY .env | cut -d= -f2 | tr -d '\r\n')" | head -c 80

The last command confirms the OpenWeather key is live. If it returns code 401,
the key is invalid or not yet activated: run the defence with
`--assume-severity` and present the degradation path as the main scenario.

## 1. Independent startup and architecture (2 min)

Start the custom server on its own, without the agent:

    npx @modelcontextprotocol/inspector .venv/bin/python server.py

Show: Connected, transport STDIO, List Tools, three tools with schemas and the
READ-ONLY / IDEMPOTENT annotations.

Say: three processes run during the demonstration. The agent starts both
servers as child processes and talks to each over stdio, meaning JSON-RPC
through standard input and output, with no ports and no network between agent
and server. The custom server owns the school registry; the external server
owns the forecast.

Stop the inspector with Ctrl+C.

## 2. External server inside the flow (2-3 min)

    python agent.py --generators 3

Point at the first two lines: both MCP connections discovered, four tools
total. Then follow the call trace on screen and state the contract of the
external tool:

- name `weather`; arguments `city` (required, English names only), `units`,
  `lang`; returns plain text with current conditions and a five-day forecast
  in three-hour slots;
- failure conditions: missing or invalid API key, unresolvable city, network
  timeout; side effect: an outbound HTTPS call to api.openweathermap.org;
- it is annotated as destructive and open-world by default because the author
  set no annotations, whereas the custom tools declare themselves read-only.

Trace one value end to end: the minimum `Low` across the slots of one date
becomes `temp_min_c` for that day, which drives `base_severity`, which drives
`overall_severity`, which is passed as `severity` into both remaining tools.

## 3. Custom workflow end to end (3-4 min)

Same run. Show that the severity class produced by tool one is the input of
tools two and three, and that the final answer contains only figures that
appeared in tool results.

Explain one contract in depth, `edu_plan_learning_mode`: input `severity`,
`generators_available` (0-100), optional `district`; output a per-school plan,
an allocation block, a summary, and the count of excluded invalid records.

Design decision to explain: candidates are filtered before ranking. Only
schools whose status would improve with a generator enter the queue, and only
then are they ordered by `students_primary`. Ranking first and filtering after
would produce a queue containing schools that the resource cannot help.

## 4. Changed valid input (part of the same segment)

    python agent.py --generators 3 --assume-severity critical

Same registry, different severity class, different plan. Name the numbers that
moved. This is also the winter scenario: in August the live forecast can only
produce `normal`, so the cold-weather behaviour is shown through an explicit
assumption, which the answer labels as such.

Second variation if asked:

    python agent.py --generators 100 --assume-severity critical

Only five generators are issued out of a hundred. The remaining schools are
limited by heating capability, not by power, so more generators change
nothing. That is the management conclusion the tool set produces.

## 5. Failure scenario (2 min)

    python agent.py --city Qxzvbn --generators 3

Show: the agent does not crash, the terminal states the forecast is
unavailable and why, the model receives a conservative `severe` assumption,
and the final answer carries an explicit warning that it is not based on an
actual forecast.

Alternative failure if asked for a different one:

    OWM_API_KEY=invalid python agent.py --generators 3

## 6. Likely questions

- Three processes; the agent starts both servers.
- The MCP client lives inside `agent.py`; the servers do not know about each
  other.
- stdio was chosen because both servers are local and single-user; HTTP would
  add ports, addresses and lifecycle for no benefit.
- Tools are discovered through `list_tools()`; names, descriptions and schemas
  come from the server, not from the agent.
- Empty result versus error: an unknown district raises `UNKNOWN_DISTRICT`
  with the list of valid districts; a district with no matching schools
  returns `assessed: []` and `total_matched: 0`, which is a success.
- `SCH-041` is defective on purpose and appears in `invalid_records` with the
  reason, to show that validation excludes rather than silently drops.
- `mcp` is pinned below 2.0 because `mcp.server.fastmcp` was removed there.
- Moving `severe_below` from -12 to -10 would push borderline days into the
  milder class and reduce the number of schools sent online; the file is
  `config/thresholds.json` and the version appears in every response.
