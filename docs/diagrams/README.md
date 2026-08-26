# Diagramas de arquitectura

Los diagramas se escriben como código en el DSL de [Eraser](https://app.eraser.io) y se versionan acá.
El `.eraser` es la fuente; la imagen exportada es lo que consumen el README y el informe.

| Fuente | Export | Se usa en |
|---|---|---|
| `parte_a_ingesta.eraser` | `Parte_A-Ingesta.png` | `README.md`, `docs/INFORME_TECNICO_LAB3.md` |
| `parte_b_analytics.eraser` | `Parte_B-analytics.png` | `README.md`, `docs/INFORME_TECNICO_LAB3.md` |

## Cómo generarlos

1. Entrar a [app.eraser.io](https://app.eraser.io) y crear un archivo nuevo.
2. Elegir el modo **Diagram as code** (el panel de código a la izquierda).
3. Pegar el contenido del `.eraser` completo, incluidos los comentarios.
4. Ajustar los íconos que no resuelvan — ver la nota de abajo.
5. **Export** y guardar en esta carpeta con el nombre de la tabla.

Los diagramas actuales están exportados en **PNG**. Si en algún momento se regeneran, conviene exportar
en **SVG**: escala sin pixelarse y se ve nítido al imprimir el informe a PDF, donde un PNG a 900 px de
ancho se nota. Para el README de GitHub los dos formatos rinden igual.

> Al cambiar de formato hay que actualizar las rutas en `README.md` y en el informe. Y usar siempre
> **barras normales** en las rutas: GitHub las resuelve como URL, y una ruta con barras invertidas de
> Windows deja la imagen rota.

## Nota sobre los íconos

Los nombres de íconos del DSL (`aws-lambda`, `aws-textract`, `aws-step-functions`…) pueden variar entre
versiones de Eraser. Si alguno no resuelve, el nodo aparece sin ícono pero el diagrama se dibuja igual:
el editor tiene autocompletado, así que se corrige escribiendo `icon:` y eligiendo de la lista.

Los que más probablemente haya que revisar son `aws-textract`, `aws-api-gateway` (usado para el
AgentCore Gateway, que no tiene ícono propio) y `aws-bedrock`.

**Si se corrige un ícono en el editor, hay que traer el cambio de vuelta al `.eraser`.** Si no, la
próxima persona que regenere el diagrama vuelve a tropezar con lo mismo.

## Por qué dos diagramas y no uno

Las dos partes son independientes hasta la prueba end-to-end: la Parte B se desarrolló y probó contra el
dataset sin esperar a que la ingesta funcionara. Un único diagrama que las mezclara sugeriría un
acoplamiento que no existe.

El punto de encuentro es la tabla sábana, y por eso `curated booking_analytics` aparece en los dos: en
el A como destino de escritura, en el B como fuente de lectura. Quien mire los dos diagramas ve dónde se
tocan sin necesidad de un tercero.
