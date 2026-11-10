# SPDX-License-Identifier: Apache-2.0
"""Runtime stand-ins for the old tests/fixtures files.

Each helper writes one temp file. Nothing under tests/fixtures is checked in.
"""

from __future__ import annotations

import json
from pathlib import Path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_messy_jsonl(directory: Path) -> Path:
    good = (
        '{"model":"gpt-4o-mini","provider":"openai","endpoint":"support.reply",'
        '"start_time":"2026-10-01T00:00:00Z","input_tokens":1000,"output_tokens":100,'
        '"duration_ms":120,"prompt":"CANARY_PROMPT_DO_NOT_LEAK",'
        '"attrs":{"env":"prod","api_key":"secret-key"}}'
    )
    missing_input = (
        '{"model":"gpt-4o-mini","provider":"openai",'
        '"start_time":"2026-10-01T01:00:00Z","output_tokens":10}'
    )
    bad_tokens = (
        '{"model":"gpt-4o-mini","provider":"openai",'
        '"start_time":"2026-10-01T02:00:00Z","input_tokens":"nope","output_tokens":10}'
    )
    missing_model = (
        '{"provider":"openai","start_time":"2026-10-01T03:00:00Z",'
        '"input_tokens":10,"output_tokens":1}'
    )
    text = "\n".join(
        [
            good,
            "",
            "not json at all CANARY_BROKEN_LINE",
            missing_input,
            bad_tokens,
            "",
            missing_model,
            "[1, 2, 3]",
            "",
        ]
    )
    return _write(directory / "messy.jsonl", text)


def write_pretty_json(directory: Path) -> Path:
    document = {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "endpoint": "support.reply",
        "start_time": "2026-10-01T00:00:00Z",
        "input_tokens": 1000,
        "output_tokens": 100,
        "duration_ms": 150,
        "ignored_field": "not-stored",
    }
    return _write(directory / "pretty.json", json.dumps(document, indent=2) + "\n")


def write_array_json(directory: Path) -> Path:
    document = [
        {
            "model": "gpt-4o-mini",
            "provider": "openai",
            "endpoint": "a",
            "start_time": "2026-10-01T00:00:00Z",
            "input_tokens": 10,
            "output_tokens": 1,
        },
        {
            "model": "gpt-4o-mini",
            "provider": "openai",
            "start_time": "2026-10-01T00:01:00Z",
            "output_tokens": 1,
        },
    ]
    return _write(directory / "array.json", json.dumps(document, indent=2) + "\n")


def write_otel_legacy(directory: Path) -> Path:
    document = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "legacy-app"}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "abc",
                                "spanId": "span-1",
                                "name": "chat gpt-4o-mini",
                                "startTimeUnixNano": "1760000000000000000",
                                "endTimeUnixNano": "1760000000150000000",
                                "attributes": [
                                    {
                                        "key": "traceloop.span.kind",
                                        "value": {"stringValue": "llm"},
                                    },
                                    {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                                    {
                                        "key": "gen_ai.request.model",
                                        "value": {"stringValue": "gpt-4o-mini"},
                                    },
                                    {
                                        "key": "gen_ai.usage.prompt_tokens",
                                        "value": {"intValue": "1000"},
                                    },
                                    {
                                        "key": "gen_ai.usage.completion_tokens",
                                        "value": {"intValue": "50"},
                                    },
                                    {
                                        "key": "llm.input_messages",
                                        "value": {"stringValue": "CANARY_OTEL_PROMPT"},
                                    },
                                ],
                                "status": {"code": "STATUS_CODE_OK"},
                            }
                        ]
                    }
                ],
            }
        ]
    }
    return _write(directory / "legacy.json", json.dumps(document, indent=2) + "\n")


def write_otel_lines(directory: Path) -> Path:
    good = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "l1",
                                "spanId": "s1",
                                "name": "chat",
                                "startTimeUnixNano": "1760000000000000000",
                                "endTimeUnixNano": "1760000000100000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.provider.name",
                                        "value": {"stringValue": "openai"},
                                    },
                                    {
                                        "key": "gen_ai.response.model",
                                        "value": {"stringValue": "gpt-4o-mini"},
                                    },
                                    {
                                        "key": "gen_ai.usage.input_tokens",
                                        "value": {"intValue": "10"},
                                    },
                                    {
                                        "key": "gen_ai.usage.output_tokens",
                                        "value": {"intValue": "2"},
                                    },
                                ],
                                "status": {"code": "STATUS_CODE_OK"},
                            }
                        ]
                    }
                ]
            }
        ]
    }
    bad = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "l2",
                                "spanId": "s2",
                                "name": "chat",
                                "attributes": [
                                    {
                                        "key": "gen_ai.request.model",
                                        "value": {"stringValue": "gpt-4o-mini"},
                                    }
                                ],
                                "status": {"code": 2},
                            }
                        ]
                    }
                ]
            }
        ]
    }
    text = json.dumps(good) + "\nthis is not json\n" + json.dumps(bad) + "\n"
    return _write(directory / "lines.jsonl", text)


def write_otel_openinference(directory: Path) -> Path:
    document = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "oi-trace",
                                "spanId": "oi-span",
                                "name": "llm",
                                "startTimeUnixNano": "1760000000000000000",
                                "endTimeUnixNano": "1760000000200000000",
                                "attributes": [
                                    {
                                        "key": "openinference.span.kind",
                                        "value": {"stringValue": "LLM"},
                                    },
                                    {"key": "llm.provider", "value": {"stringValue": "openai"}},
                                    {
                                        "key": "llm.model_name",
                                        "value": {"stringValue": "gpt-4o-mini"},
                                    },
                                    {
                                        "key": "llm.token_count.prompt",
                                        "value": {"intValue": 800},
                                    },
                                    {
                                        "key": "llm.token_count.completion",
                                        "value": {"intValue": 20},
                                    },
                                    {
                                        "key": "llm.token_count.prompt_details.cache_read",
                                        "value": {"intValue": "100"},
                                    },
                                    {
                                        "key": "tracetally.endpoint",
                                        "value": {"stringValue": "support.reply"},
                                    },
                                ],
                                "status": {"code": 1},
                            },
                            {
                                "traceId": "oi-trace",
                                "spanId": "oi-tool",
                                "name": "lookup",
                                "startTimeUnixNano": "1760000000000000000",
                                "endTimeUnixNano": "1760000000010000000",
                                "attributes": [
                                    {
                                        "key": "openinference.span.kind",
                                        "value": {"stringValue": "RETRIEVER"},
                                    }
                                ],
                                "status": {"code": 1},
                            },
                        ]
                    }
                ]
            }
        ]
    }
    return _write(directory / "openinference.json", json.dumps(document, indent=2) + "\n")


def write_langfuse_observations(directory: Path) -> Path:
    document = [
        {
            "id": "gen-1",
            "traceId": "trace-1",
            "type": "GENERATION",
            "name": "support.reply",
            "startTime": "2026-10-01T00:00:00.000Z",
            "endTime": "2026-10-01T00:00:01.500Z",
            "model": "gpt-4o-mini",
            "input": [{"role": "system", "content": "CANARY_PROMPT_DO_NOT_LEAK"}],
            "output": "CANARY_OUTPUT_DO_NOT_LEAK",
            "usageDetails": {
                "input": 1000,
                "output": 100,
                "input_cache_read": 200,
                "output_reasoning": 40,
                "total": 1100,
            },
            "costDetails": {"input": 0.0007, "output": 0.0003, "total": 0.001},
            "level": "DEFAULT",
            "metadata": {"feature": "reply", "provider": "openai", "note": "CANARY_META"},
            "userId": "user-1",
            "sessionId": "sess-1",
            "comment": "ignored extra field",
        },
        {
            "id": "tool-1",
            "traceId": "trace-1",
            "type": "TOOL",
            "name": "search",
            "startTime": "2026-10-01T00:00:00.100Z",
            "endTime": "2026-10-01T00:00:00.400Z",
            "input": {"query": "CANARY_TOOL"},
            "level": "DEFAULT",
            "parentObservationId": "gen-1",
        },
        {
            "id": "bad-1",
            "traceId": "trace-1",
            "type": "GENERATION",
            "name": "broken",
            "startTime": "2026-10-01T00:00:02Z",
            "level": "ERROR",
            "statusMessage": "429 rate limit",
        },
        {
            "id": "span-1",
            "traceId": "trace-2",
            "type": "RETRIEVER",
            "name": "retrieve",
            "startTime": "2026-10-01 00:00:03",
            "latency": 0.25,
            "level": "DEFAULT",
        },
    ]
    return _write(directory / "observations.json", json.dumps(document, indent=2) + "\n")


def write_langfuse_csv(directory: Path) -> Path:
    header = (
        "ID,Trace ID,Type,Name,Start Time,End Time,Model,Input Tokens,"
        "Output Tokens,Total Cost ($),Level,User ID,Session ID,"
        "Parent Observation ID,Status Message,Comment"
    )
    rows = (
        "gen-2,trace-2,GENERATION,support.reply,2026/10/02 00:00:00,"
        "2026/10/02 00:00:01,gpt-4o-mini,500,25,0.004,DEFAULT,user-2,sess-2,,,kept",
        "tool-2,trace-2,TOOL,search,21/10/2026 00:00:00,,search.docs,,,,,"
        "user-2,sess-2,gen-2,,ignored",
        "bad-2,trace-2,GENERATION,broken,2026-10-02T00:00:03Z,,,,,,,ERROR,"
        "user-2,sess-2,,429 too many requests,x",
        "span-2,trace-2,AGENT,planner,2026-10-02T00:00:04Z,,,,,,,,,user-2,sess-2,gen-2,,",
    )
    text = header + "\n" + "\n".join(rows) + "\n"
    return _write(directory / "observations.csv", text)


def write_langfuse_traces(directory: Path) -> Path:
    document = {
        "data": [
            {
                "id": "trace-9",
                "timestamp": "2026-10-03T00:00:00Z",
                "name": "support.draft",
                "userId": "user-9",
                "sessionId": "sess-9",
                "tags": ["batch"],
                "observations": [
                    {
                        "id": "gen-9",
                        "type": "GENERATION",
                        "name": "draft",
                        "startTime": "2026-10-03T00:00:00Z",
                        "endTime": "2026-10-03T00:00:02Z",
                        "model": "claude-sonnet-4-5",
                        "usage": {"input": 100, "output": 50, "unit": "TOKENS"},
                        "level": "ERROR",
                        "statusMessage": "HTTP 503 upstream",
                    },
                    {
                        "id": "emb-9",
                        "type": "EMBEDDING",
                        "name": "embed",
                        "startTime": "2026-10-03T00:00:02Z",
                        "model": "text-embedding-3-small",
                        "usageDetails": {"input": 20, "output": 0},
                    },
                ],
            },
            {
                "id": "trace-empty",
                "timestamp": "2026-10-03T01:00:00Z",
                "name": "no-model",
                "inputTokens": 10,
                "outputTokens": 1,
            },
        ]
    }
    return _write(directory / "traces.json", json.dumps(document, indent=2) + "\n")


def write_openai_usage_csv(directory: Path) -> Path:
    header = (
        "start_time,model,project_id,num_model_requests,input_tokens,output_tokens,"
        "input_cached_tokens,output_reasoning_tokens,batch,service_tier,notes"
    )
    rows = (
        "2026-10-01T00:00:00Z,gpt-4o-mini,proj_a,10,1000000,2000,200000,0,false,default,extra-note",
        "1727740800,gpt-4o,proj_b,2,0,1000,0,0,true,default,also-extra",
        "not-a-date,gpt-4o-mini,proj_a,1,10,10,0,0,false,default,bad-row",
    )
    return _write(directory / "usage.csv", header + "\n" + "\n".join(rows) + "\n")


def write_openai_costs_csv(directory: Path) -> Path:
    text = """\
start_time;line_item;amount_value;amount_currency;project_name;remark
2026-10-02 00:00:00;gpt-4o-mini-2024-07-18, input;1.25;usd;Acme;ignored
2026-10-02 00:00:00;not a model name;0.10;eur;Acme;bad-currency
"""
    return _write(directory / "costs.csv", text)
