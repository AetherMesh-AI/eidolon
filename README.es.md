# Eidolon

[English](README.md) · [中文](README.zh-CN.md) · [اردو](README.ur-pk.md)

Eidolon es el gestor de agentes de escritorio de código abierto de AetherMesh, actualmente en desarrollo. Su objetivo es dar a cada agente una identidad continua, una conversación propia y contexto útil que pueda acompañar el trabajo a lo largo del tiempo.

El proyecto reúne conversaciones, datos locales y trabajo coordinado en una aplicación. La intención es poder volver a un agente, retomar la conversación y entender qué está haciendo sin administrar sesiones desconectadas entre sí.

El SDK previsto `AetherMesh-core` conectará aplicaciones con la red de IA entre pares de AetherMesh. Eidolon contempla esa conexión como una integración opcional, conservando los proveedores de IA existentes, su autenticación, credenciales, selección de modelos y demás integraciones. Nous Portal, OpenRouter y OpenAI siguen siendo servicios de sus respectivos proveedores. Esta dirección del proyecto no es una capacidad de red verificada.

> **Alfa temprana / prueba de concepto.** Consulta las [versiones de Eidolon](https://github.com/AetherMesh-AI/Eidolon/releases) para conocer los artefactos, instrucciones y límites de cada compilación. La publicación de un artefacto no implica que esté listo para producción ni que existan instaladores aceptados para todas las plataformas.

## La experiencia que buscamos

- Conversaciones continuas con agentes que mantengan su propia identidad y función.
- Trabajo coordinado con responsables, decisiones y necesidades de intervención visibles.
- Memoria útil y compartición deliberada de conocimiento, sin dar a todos los agentes acceso a todas las conversaciones.
- Control claro del usuario sobre permisos, acceso y acciones relevantes.
- Recuperación fiable ante fallos y conservación de las conversaciones.

Son objetivos de desarrollo; no todos los mecanismos de memoria, permisos o coordinación están implementados o verificados.

## Estado actual y límites

La aplicación de escritorio incluye conversaciones por agente y vistas de inicio, objetivos, organización, actividad, conocimiento y capacidades. La presencia de estas vistas no demuestra que exista un sistema de organización conectado y operativo.

Los objetivos, planes, equipos de ejemplo y resultados de la organización son prototipos. Crear un objetivo no envía trabajo a agentes. Los datos de ejemplo son ficticios y sus registros se guardan en `window.localStorage`, separados del almacenamiento de conversaciones. Una aprobación local no concede permisos al entorno de ejecución y registrar un resultado no demuestra que se haya ejecutado trabajo.

El directorio de datos predeterminado es `~/.eidolon`. Pruebas nativas acotadas han verificado el inicio, la identidad visual y la persistencia de conversaciones sintéticas tras reiniciar. La memoria local y entre agentes sigue siendo un prototipo. La primera transición al segundo agente, la limpieza de procesos, las conversaciones reales con IA y la aceptación completa de la aplicación instalada requieren más verificación.

Las actualizaciones basadas en Git usan este repositorio. Una prueba de aplicación instalada en macOS Apple Silicon verificó el relanzamiento, la conservación de una conversación sintética y la recuperación ante fallos de descarga y del comando de empaquetado. Se usaron transporte Git local y una aplicación con firma ad hoc, sin notarización. Esto no demuestra inferencia de modelos, compatibilidad de Windows/Linux ni recuperación ante toda interrupción. Esta alfa inicial es una publicación de código fuente, no una garantía de binarios aceptados para todas las plataformas.

## Probar Eidolon

Usa datos desechables con las compilaciones experimentales y conserva copias independientes de la información importante. No las uses como única copia de conversaciones o proyectos.

Antes de instalar, revisa las [versiones del proyecto](https://github.com/AetherMesh-AI/Eidolon/releases), sus instrucciones específicas, el alcance de las pruebas y las limitaciones conocidas. Este documento no establece un instalador probado ni una vía de actualización compatible para tu plataforma.

## Contribuir y comunicar problemas

Consulta [CONTRIBUTING.es.md](CONTRIBUTING.es.md) y [SECURITY.es.md](SECURITY.es.md). Los informes deben explicar el comportamiento esperado, el observado y una reproducción sin conversaciones privadas ni credenciales. Los comandos de desarrollo y la existencia del código no garantizan una instalación aceptada.

Este resumen sigue el alcance del [README en inglés](README.md). Algunos documentos heredados todavía describen el proyecto original; no constituyen pruebas de aceptación de Eidolon.

## Origen y licencia

Eidolon parte del código de Hermes Agent de Nous Research y se desarrolla como un proyecto independiente. Se conservan la atribución original y los avisos de la licencia MIT aplicables en [LICENSE](LICENSE) y en el código.
