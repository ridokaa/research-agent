"""Script to generate Postman Collection (v2.1) and Postman Environment for agentic-loop."""
import json
import os
import uuid

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTMAN_DIR = os.path.join(BASE_DIR, "docs", "postman")

def generate_environment() -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": "Agentic Loop — Local",
        "values": [
            {
                "key": "baseUrl",
                "value": "http://localhost:8000",
                "type": "default",
                "enabled": True
            },
            {
                "key": "run_id",
                "value": "",
                "type": "default",
                "enabled": True
            },
            {
                "key": "conversation_id",
                "value": "",
                "type": "default",
                "enabled": True
            }
        ],
        "_postman_variable_scope": "environment",
        "_postman_exported_at": "2026-08-03T03:37:19.000Z",
        "_postman_exported_using": "Postman/10.0.0"
    }

def generate_collection() -> dict:
    req_id_header = {
        "key": "X-Request-ID",
        "value": "req-12345678-abc-90",
        "description": "Optional request ID for correlation and tracing"
    }

    dummy_run_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    dummy_conv_id = "c1d2e3f4-a5b6-7890-1234-567890abcdef"

    collection = {
        "info": {
            "_postman_id": str(uuid.uuid4()),
            "name": "Agentic Loop API",
            "description": "Postman Collection v2.1.0 untuk Agentic Loop API (v0.1.0).\n\nTidak ada autentikasi inbound (No Auth). Semua request menggunakan `{{baseUrl}}`.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "auth": {
            "type": "noauth"
        },
        "event": [
            {
                "listen": "prerequest",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "if (!pm.request.headers.has('X-Request-ID')) {",
                        "    pm.request.headers.upsert({ key: 'X-Request-ID', value: pm.variables.replaceIn('{{$guid}}') });",
                        "}"
                    ]
                }
            }
        ],
        "item": [
            # Folder 1: Agent
            {
                "name": "Agent",
                "item": [
                    {
                        "name": "Query Agent (sync)",
                        "request": {
                            "method": "POST",
                            "header": [
                                {"key": "Content-Type", "value": "application/json"},
                                req_id_header
                            ],
                            "body": {
                                "mode": "raw",
                                "raw": json.dumps({
                                    "query": "Apa yang sedang tren di Verosight minggu ini?",
                                    "conversation_id": None,
                                    "options": {
                                        "max_iterations": 8,
                                        "credits_budget": 100,
                                        "timeout_s": 180.0,
                                        "parallel_tools": True,
                                        "store": True,
                                        "tools_allow": None,
                                        "tools_deny": None
                                    }
                                }, indent=2),
                                "options": {"raw": {"language": "json"}}
                            },
                            "url": {
                                "raw": "{{baseUrl}}/v1/agent/query",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "agent", "query"]
                            },
                            "description": "Execute agent query synchronously and get final answer."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "const json = pm.response.json();",
                                        "if (json.run_id) {",
                                        "    pm.environment.set('run_id', json.run_id);",
                                        "}",
                                        "if (json.conversation_id) {",
                                        "    pm.environment.set('conversation_id', json.conversation_id);",
                                        "}",
                                        "pm.test('Response contains answer string', function () {",
                                        "    pm.expect(json.answer).to.be.a('string').and.not.empty;",
                                        "});",
                                        "pm.test('Response header X-Request-ID exists', function () {",
                                        "    pm.response.to.have.header('X-Request-ID');",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success",
                                "originalRequest": {
                                    "method": "POST",
                                    "header": [{"key": "Content-Type", "value": "application/json"}],
                                    "body": {"mode": "raw", "raw": "{\n  \"query\": \"Apa yang sedang tren di Verosight minggu ini?\"\n}"},
                                    "url": {"raw": "{{baseUrl}}/v1/agent/query", "host": ["{{baseUrl}}"], "path": ["v1", "agent", "query"]}
                                },
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "answer": "Berdasarkan analisis Verosight minggu ini, topik utama yang sedang tren adalah...",
                                    "run_id": dummy_run_id,
                                    "conversation_id": dummy_conv_id,
                                    "status": "success",
                                    "iterations": 2,
                                    "latency_ms": 1450
                                }, indent=2)
                            },
                            {
                                "name": "422 Validation Error",
                                "status": "Unprocessable Entity",
                                "code": 422,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {
                                        "code": "VALIDATION_ERROR",
                                        "message": "Validation error: [{'type': 'string_type', 'loc': ('body', 'query'), 'msg': 'Input should be a valid string'}]"
                                    },
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "429 Rate Limit Exceeded",
                                "status": "Too Many Requests",
                                "code": 429,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {
                                        "code": 429,
                                        "message": "Rate limit exceeded. Maximum 60 requests per minute."
                                    },
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {
                                        "code": "INTERNAL_SERVER_ERROR",
                                        "message": "An unexpected server error occurred."
                                    },
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    },
                    {
                        "name": "Query Agent (SSE stream)",
                        "request": {
                            "method": "POST",
                            "header": [
                                {"key": "Content-Type", "value": "application/json"},
                                req_id_header
                            ],
                            "body": {
                                "mode": "raw",
                                "raw": json.dumps({
                                    "query": "Apa yang sedang tren di Verosight minggu ini?",
                                    "conversation_id": None,
                                    "options": {
                                        "max_iterations": 8,
                                        "credits_budget": 100
                                    }
                                }, indent=2),
                                "options": {"raw": {"language": "json"}}
                            },
                            "url": {
                                "raw": "{{baseUrl}}/v1/agent/query/stream",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "agent", "query", "stream"]
                            },
                            "description": "Stream final answer tokens using Server-Sent Events (text/event-stream)."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "pm.test('Content-Type is text/event-stream', function () {",
                                        "    pm.expect(pm.response.headers.get('Content-Type')).to.include('text/event-stream');",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success (SSE stream)",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "plain",
                                "header": [
                                    {"key": "Content-Type", "value": "text/event-stream"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": "data: Berdasarkan \n\ndata: analisis \n\ndata: Verosight...\n\ndata: [DONE]\n\n"
                            },
                            {
                                "name": "422 Validation Error",
                                "status": "Unprocessable Entity",
                                "code": 422,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "VALIDATION_ERROR", "message": "Validation error: [...]"},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "429 Rate Limit Exceeded",
                                "status": "Too Many Requests",
                                "code": 429,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": 429, "message": "Rate limit exceeded. Maximum 60 requests per minute."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    }
                ]
            },
            # Folder 2: Runs
            {
                "name": "Runs",
                "item": [
                    {
                        "name": "List Runs",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/v1/runs?limit=20&offset=0",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "runs"],
                                "query": [
                                    {"key": "limit", "value": "20"},
                                    {"key": "offset", "value": "0"},
                                    {"key": "status", "value": "success", "disabled": True}
                                ]
                            },
                            "description": "List execution runs with pagination and optional status filter."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "pm.test('Response is an array', function () {",
                                        "    pm.expect(pm.response.json()).to.be.an('array');",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps([
                                    {
                                        "id": dummy_run_id,
                                        "conversation_id": dummy_conv_id,
                                        "user_query": "Apa yang sedang tren di Verosight minggu ini?",
                                        "status": "success",
                                        "final_answer": "Berdasarkan analisis Verosight...",
                                        "iterations": 2,
                                        "credits_used": 15,
                                        "total_tokens": 1200,
                                        "latency_ms": 1450,
                                        "error": None,
                                        "started_at": "2026-08-03T03:30:00Z",
                                        "finished_at": "2026-08-03T03:30:01.450Z"
                                    }
                                ], indent=2)
                            },
                            {
                                "name": "422 Validation Error",
                                "status": "Unprocessable Entity",
                                "code": 422,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "VALIDATION_ERROR", "message": "Validation error: [{'type': 'int_parsing', 'loc': ('query', 'limit'), 'msg': 'Input should be a valid integer'}]"},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    },
                    {
                        "name": "Get Run",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/v1/runs/:run_id",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "runs", ":run_id"],
                                "variable": [
                                    {"key": "run_id", "value": "{{run_id}}", "description": "Run UUID"}
                                ]
                            },
                            "description": "Get run metadata by ID."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "const json = pm.response.json();",
                                        "pm.test('Run ID matches', function () {",
                                        "    pm.expect(json.id).to.equal(pm.environment.get('run_id') || json.id);",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "id": dummy_run_id,
                                    "conversation_id": dummy_conv_id,
                                    "user_query": "Apa yang sedang tren di Verosight minggu ini?",
                                    "status": "success",
                                    "final_answer": "Berdasarkan analisis Verosight...",
                                    "iterations": 2,
                                    "credits_used": 15,
                                    "total_tokens": 1200,
                                    "latency_ms": 1450,
                                    "error": None,
                                    "started_at": "2026-08-03T03:30:00Z",
                                    "finished_at": "2026-08-03T03:30:01.450Z"
                                }, indent=2)
                            },
                            {
                                "name": "404 Not Found",
                                "status": "Not Found",
                                "code": 404,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "404", "message": f"Run '{dummy_run_id}' not found."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "422 Validation Error",
                                "status": "Unprocessable Entity",
                                "code": 422,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "VALIDATION_ERROR", "message": "Validation error: [{'type': 'uuid_parsing', 'loc': ('path', 'run_id'), 'msg': 'Input should be a valid UUID'}]"},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    },
                    {
                        "name": "Get Run Trace (JSON)",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/v1/runs/:run_id/trace",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "runs", ":run_id", "trace"],
                                "variable": [
                                    {"key": "run_id", "value": "{{run_id}}", "description": "Run UUID"}
                                ]
                            },
                            "description": "Get full execution trace as JSON array of events."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "pm.test('Trace is an array', function () {",
                                        "    pm.expect(pm.response.json()).to.be.an('array');",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success (JSON)",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps([
                                    {
                                        "run_id": dummy_run_id,
                                        "seq": 0,
                                        "iteration": 0,
                                        "type": "run_start",
                                        "summary": "Run started",
                                        "payload": {"query": "Apa yang sedang tren?"},
                                        "duration_ms": 10,
                                        "created_at": "2026-08-03T03:30:00Z"
                                    },
                                    {
                                        "run_id": dummy_run_id,
                                        "seq": 1,
                                        "iteration": 1,
                                        "type": "tool_call",
                                        "summary": "Called verosight_trending",
                                        "payload": {"tool": "verosight_trending"},
                                        "duration_ms": 320,
                                        "created_at": "2026-08-03T03:30:00.500Z"
                                    }
                                ], indent=2)
                            },
                            {
                                "name": "404 Not Found",
                                "status": "Not Found",
                                "code": 404,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "404", "message": f"Run '{dummy_run_id}' not found."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "422 Validation Error",
                                "status": "Unprocessable Entity",
                                "code": 422,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "VALIDATION_ERROR", "message": "Validation error: [...]"},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    },
                    {
                        "name": "Get Run Trace (text)",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/v1/runs/:run_id/trace?format=text",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "runs", ":run_id", "trace"],
                                "query": [
                                    {"key": "format", "value": "text"}
                                ],
                                "variable": [
                                    {"key": "run_id", "value": "{{run_id}}", "description": "Run UUID"}
                                ]
                            },
                            "description": "Get execution trace formatted as plain text tree."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "pm.test('Content-Type is text/plain', function () {",
                                        "    pm.expect(pm.response.headers.get('Content-Type')).to.include('text/plain');",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success (text/plain tree)",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "plain",
                                "header": [
                                    {"key": "Content-Type", "value": "text/plain; charset=utf-8"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": f"RUN TRACE [{dummy_run_id}]\n├── [seq 0] [iter 0] run_start: Run started\n├── [seq 1] [iter 1] tool_call: Called verosight_trending (320ms)\n└── [seq 2] [iter 2] final_answer: Berdasarkan analisis Verosight..."
                            },
                            {
                                "name": "404 Not Found",
                                "status": "Not Found",
                                "code": 404,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "404", "message": f"Run '{dummy_run_id}' not found."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "422 Validation Error",
                                "status": "Unprocessable Entity",
                                "code": 422,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "VALIDATION_ERROR", "message": "Validation error: [...]"},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    },
                    {
                        "name": "Get Run Tool Invocations",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/v1/runs/:run_id/tools",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "runs", ":run_id", "tools"],
                                "variable": [
                                    {"key": "run_id", "value": "{{run_id}}", "description": "Run UUID"}
                                ]
                            },
                            "description": "List tool invocations recorded during a run."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "pm.test('Tool invocations is an array', function () {",
                                        "    pm.expect(pm.response.json()).to.be.an('array');",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps([
                                    {
                                        "id": "t1a2b3c4-d5e6-7890-abcd-ef1234567890",
                                        "run_id": dummy_run_id,
                                        "step_seq": 1,
                                        "tool_name": "verosight_trending",
                                        "source": "verosight",
                                        "arguments": {"category": "all"},
                                        "result": {"trends": ["AI Agents", "FastAPI"]},
                                        "status": "success",
                                        "error": None,
                                        "duration_ms": 320,
                                        "credits_used": 5,
                                        "created_at": "2026-08-03T03:30:00.500Z"
                                    }
                                ], indent=2)
                            },
                            {
                                "name": "404 Not Found",
                                "status": "Not Found",
                                "code": 404,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "404", "message": f"Run '{dummy_run_id}' not found."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "422 Validation Error",
                                "status": "Unprocessable Entity",
                                "code": 422,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "VALIDATION_ERROR", "message": "Validation error: [...]"},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    }
                ]
            },
            # Folder 3: Conversations
            {
                "name": "Conversations",
                "item": [
                    {
                        "name": "List Conversations",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/v1/conversations?limit=20&offset=0",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "conversations"],
                                "query": [
                                    {"key": "limit", "value": "20"},
                                    {"key": "offset", "value": "0"}
                                ]
                            },
                            "description": "List existing conversations."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "pm.test('Conversations is an array', function () {",
                                        "    pm.expect(pm.response.json()).to.be.an('array');",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps([
                                    {
                                        "id": dummy_conv_id,
                                        "title": "Verosight Analysis",
                                        "metadata": {"user": "demo"},
                                        "created_at": "2026-08-03T03:30:00Z",
                                        "updated_at": "2026-08-03T03:30:01.450Z"
                                    }
                                ], indent=2)
                            },
                            {
                                "name": "422 Validation Error",
                                "status": "Unprocessable Entity",
                                "code": 422,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "VALIDATION_ERROR", "message": "Validation error: [...]"},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    },
                    {
                        "name": "Get Conversation",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/v1/conversations/:conversation_id",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "conversations", ":conversation_id"],
                                "variable": [
                                    {"key": "conversation_id", "value": "{{conversation_id}}", "description": "Conversation UUID"}
                                ]
                            },
                            "description": "Get conversation metadata by ID."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "const json = pm.response.json();",
                                        "pm.test('Conversation ID matches', function () {",
                                        "    pm.expect(json.id).to.equal(pm.environment.get('conversation_id') || json.id);",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "id": dummy_conv_id,
                                    "title": "Verosight Analysis",
                                    "metadata": {"user": "demo"},
                                    "created_at": "2026-08-03T03:30:00Z",
                                    "updated_at": "2026-08-03T03:30:01.450Z"
                                }, indent=2)
                            },
                            {
                                "name": "404 Not Found",
                                "status": "Not Found",
                                "code": 404,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "404", "message": f"Conversation '{dummy_conv_id}' not found."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "422 Validation Error",
                                "status": "Unprocessable Entity",
                                "code": 422,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "VALIDATION_ERROR", "message": "Validation error: [...]"},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    }
                ]
            },
            # Folder 4: Tools
            {
                "name": "Tools",
                "item": [
                    {
                        "name": "List Active Tools",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/v1/tools",
                                "host": ["{{baseUrl}}"],
                                "path": ["v1", "tools"]
                            },
                            "description": "List currently registered and active tools."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "pm.test('Tools list is an array', function () {",
                                        "    pm.expect(pm.response.json()).to.be.an('array');",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps([
                                    {
                                        "name": "verosight_trending",
                                        "source": "verosight",
                                        "description": "Fetch trending topics from Verosight.",
                                        "cost_credits": 5,
                                        "available": True,
                                        "parameters": {
                                            "type": "object",
                                            "properties": {
                                                "category": {"type": "string"}
                                            }
                                        },
                                        "timeout_s": 30.0,
                                        "tags": ["analytics", "verosight"]
                                    }
                                ], indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    }
                ]
            },
            # Folder 5: System
            {
                "name": "System",
                "item": [
                    {
                        "name": "Health",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/health",
                                "host": ["{{baseUrl}}"],
                                "path": ["health"]
                            },
                            "description": "Basic health check endpoint."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});",
                                        "pm.test('Status is ok', function () {",
                                        "    pm.expect(pm.response.json().status).to.equal('ok');",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({"status": "ok"}, indent=2)
                            }
                        ]
                    },
                    {
                        "name": "Health Deps",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/health/deps",
                                "host": ["{{baseUrl}}"],
                                "path": ["health", "deps"]
                            },
                            "description": "Dependency health check endpoint (DB, LLM, MCP)."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 OK (status: ok)",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "status": "ok",
                                    "db": "ok",
                                    "llm": "ok",
                                    "mcp": "ok",
                                    "details": {}
                                }, indent=2)
                            },
                            {
                                "name": "200 OK (status: degraded)",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "status": "degraded",
                                    "db": "ok",
                                    "llm": "error",
                                    "mcp": "ok",
                                    "details": {"llm": "OpenAI API timeout"}
                                }, indent=2)
                            },
                            {
                                "name": "500 Internal Server Error",
                                "status": "Internal Server Error",
                                "code": 500,
                                "_postman_previewlanguage": "json",
                                "header": [
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": json.dumps({
                                    "error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred."},
                                    "meta": {"request_id": "req-12345678-abc-90"}
                                }, indent=2)
                            }
                        ]
                    },
                    {
                        "name": "Metrics (Prometheus)",
                        "request": {
                            "method": "GET",
                            "header": [req_id_header],
                            "url": {
                                "raw": "{{baseUrl}}/metrics",
                                "host": ["{{baseUrl}}"],
                                "path": ["metrics"]
                            },
                            "description": "Expose Prometheus application metrics."
                        },
                        "event": [
                            {
                                "listen": "test",
                                "script": {
                                    "type": "text/javascript",
                                    "exec": [
                                        "pm.test('Status code is 200', function () {",
                                        "    pm.response.to.have.status(200);",
                                        "});"
                                    ]
                                }
                            }
                        ],
                        "response": [
                            {
                                "name": "200 Success (Prometheus text)",
                                "status": "OK",
                                "code": 200,
                                "_postman_previewlanguage": "plain",
                                "header": [
                                    {"key": "Content-Type", "value": "text/plain; version=0.0.4; charset=utf-8"},
                                    {"key": "X-Request-ID", "value": "req-12345678-abc-90"}
                                ],
                                "body": "# HELP http_requests_total Total number of HTTP requests\n# TYPE http_requests_total counter\nhttp_requests_total{method=\"GET\",handler=\"/health\",status=\"200\"} 42.0\n"
                            }
                        ]
                    }
                ]
            }
        ]
    }
    return collection

def main():
    os.makedirs(POSTMAN_DIR, exist_ok=True)
    env_data = generate_environment()
    col_data = generate_collection()

    env_path = os.path.join(POSTMAN_DIR, "Agentic_Loop_Local.postman_environment.json")
    col_path = os.path.join(POSTMAN_DIR, "Agentic_Loop_API.postman_collection.json")

    with open(env_path, "w", encoding="utf-8") as f:
        json.dump(env_data, f, indent=2, ensure_ascii=False)

    with open(col_path, "w", encoding="utf-8") as f:
        json.dump(col_data, f, indent=2, ensure_ascii=False)

    print(f"Postman Environment generated at: {env_path}")
    print(f"Postman Collection generated at: {col_path}")

if __name__ == "__main__":
    main()
