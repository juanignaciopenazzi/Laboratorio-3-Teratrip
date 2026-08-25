# Laboratorio Final — TeraTrip Intelligent Data & Analytics

Ingesta automática de reservas desde documentos PDF, y un agente conversacional que consulta el dataset
resultante en lenguaje natural.

Subir un PDF a S3 dispara un pipeline que extrae la tabla con Textract, la normaliza al esquema de
TeraTrip e incorpora los registros a la tabla sábana sin duplicar reservas existentes. Un agente de
Amazon Bedrock AgentCore traduce preguntas de negocio a SQL, las ejecuta contra Athena a través de una
herramienta validada y responde citando la cifra real.

> **Los recursos de AWS se crean a mano desde la consola.** Este repositorio versiona el código que los
> respalda: scripts de Glue, handlers de Lambda, la definición ASL de la Step Function, las policies IAM
> escritas a mano, los documentos de la Knowledge Base, el system prompt del agente y las pruebas.

---

## Arquitectura

**Parte A — Ingesta**

```
PDF → S3 incoming-documents/ → EventBridge → Step Functions
                                                 ├─ Lambda  Textract (TABLES)
                                                 ├─ Lambda  normalización
                                                 └─ Glue    merge con anti-join
                                              → curated/*.parquet → Athena
```

**Parte B — Analytics conversacional**

```
Usuario → Harness (Claude Haiku 4.5) → Gateway (IAM/SigV4)
                                          ├─ Knowledge Base   contexto de negocio
                                          └─ Lambda           execute_athena_query → Athena
```

El detalle está en [`docs/INFORME_TECNICO_LAB3.md`](docs/INFORME_TECNICO_LAB3.md).

---

## Estructura del repositorio

```
docs/
  LabFinal_TeraTrip.html      consigna original
  PLAN.md                     plan de implementación por fases
  schema_contract.md          contrato de esquema: 20 columnas, baseline y reglas de derivación
  INFORME_TECNICO_LAB3.md     entregable: arquitectura, problemas y preguntas de investigación

src/
  glue_jobs/
    00_raw_to_curated.py      bootstrap del dataset base desde los CSV (descartable tras Fase 0)
    merge_curated.py          merge incremental con anti-join por booking_id
  lambdas/
    textract_extract/         extracción y reconstrucción de la tabla
    normalize/                mapeo al esquema de TeraTrip
    athena_query/             herramienta execute_athena_query con validación en capas
  stepfunctions/
    ingest_pipeline.asl.json  definición de la máquina de estados
    eventbridge_rule_pattern.json
  kb/                         los 4 Markdown de la Knowledge Base
  agent/
    system_prompt.md          instrucciones del Harness
    tool_schema_execute_athena_query.json
  iam/                        policies inline escritas a mano
  queries/                    validaciones SQL para Athena
  pdf/                        generador y documentos de prueba

tests/
  test_textract_reconstruct.py   reconstrucción de tablas de Textract
  test_normalize.py              normalización al esquema
  test_sql_guard.py              validador de SQL (evidencia de la Prueba 3)
  golden_questions.md            preguntas, SQL de referencia y resultado esperado
```

---

## Cómo correr las pruebas locales

No necesitan credenciales de AWS: las funciones bajo prueba son puras.

```bash
python tests/test_textract_reconstruct.py
```
```bash
python tests/test_normalize.py
```
```bash
python tests/test_sql_guard.py
```

Para inspeccionar un JSON real descargado de `textract-raw/`:

```bash
python tests/test_textract_reconstruct.py ruta/al/archivo.json
```

Para regenerar los documentos PDF de prueba (requiere `pip install reportlab`):

```bash
python src/pdf/generate_demo_pdf.py
```

---

## El dataset

Tabla sábana `teratrip_db.booking_analytics` en Athena: **una fila por reserva**, 20 columnas, Parquet
sobre S3. Combina reservas, clientes, pagos, vuelos y hoteles ya desnormalizados, con los campos
derivados calculados.

Baseline reconstruido: **500.000 reservas**. Las columnas, sus tipos, los dominios de valores y las
reglas de derivación están en [`docs/schema_contract.md`](docs/schema_contract.md), que es la fuente
única: de ahí se derivan la normalización, el merge y el diccionario de datos de la Knowledge Base.

---

## Recursos de AWS

Región `us-east-1`, cuenta `955030484229`.

| Tipo | Nombre |
|---|---|
| S3 bucket | `teratrip-data-lake-955030484229` |
| Glue Database | `teratrip_db` |
| Glue Jobs | `glue-teratrip-bootstrap-curated`, `glue-teratrip-merge-curated` |
| Glue Crawler | `crawler-teratrip-curated` |
| Athena WorkGroup | `wg-teratrip` |
| Lambdas | `lambda-teratrip-textract-extract`, `lambda-teratrip-normalize`, `lambda-teratrip-athena-query` |
| Step Function | `sfn-teratrip-ingest` |
| EventBridge rule | `evb-teratrip-new-document` |
| Knowledge Base | `teratrip-business-kb` |
| Gateway | `teratrip-analytics-gateway` |
| Targets | `teratrip-kb-target`, `teratrip-athena-target` |
| Harness | `teratrip_analytics_agent` |

Prefijos del bucket: `raw/`, `curated/`, `curated/_backup/`, `incoming-documents/`, `incoming-data/`,
`textract-raw/`, `kb/`, `athena-results/`.

---

## Notas de operación

- **El documento `teratrip_reservas_walkthrough.pdf` está reservado para la demo end-to-end.** Su ancla
  `Puerto Madryn` no existe en el dataset, y una vez ingestado deja de servir para demostrar el
  antes/después.
- El Glue Job de merge está limitado a **una corrida concurrente**: es un read-modify-write sobre un
  archivo único, y dos merges simultáneos perderían registros sin error visible.
- La **Memory del Harness está desactivada** a propósito: recordar cifras entre sesiones sirve datos
  vencidos y hace imposible aislar una prueba.
- No hace falta volver a correr el Crawler después de un merge: el esquema no cambia y la tabla del
  catálogo apunta al prefijo, no al contenido.
