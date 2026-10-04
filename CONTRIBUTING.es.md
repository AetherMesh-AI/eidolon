# Contribuir a Eidolon

Eidolon es un proyecto independiente que desarrolla un entorno de escritorio para agentes persistentes. Lee [README.es.md](README.es.md) para distinguir entre código existente, prototipos y comportamiento verificado. Las [versiones del proyecto](https://github.com/AetherMesh-AI/Eidolon/releases) documentan el alcance y los límites de cada compilación; publicarla no demuestra que todas sus funciones y plataformas estén aceptadas.

Esta guía resume [CONTRIBUTING.md](CONTRIBUTING.md), el documento de referencia en inglés.

## Antes de empezar

- Busca problemas y trabajo previo en el [repositorio de Eidolon](https://github.com/AetherMesh-AI/Eidolon/issues). Comenta los cambios importantes antes de implementarlos.
- Lee [AGENTS.md](AGENTS.md) y las instrucciones del área que vas a modificar. Las descripciones heredadas no constituyen compromisos de soporte de Eidolon.
- Revisa el árbol de trabajo y conserva los cambios sin confirmar de otros colaboradores, su autoría y los avisos de licencia.
- Prioriza correcciones reproducibles, fiabilidad e interacciones claras. No presentes una simulación como una capacidad real.

## Orientación en el repositorio

- Escritorio: [apps/desktop](apps/desktop).
- Prototipos de organización y memoria: [apps/desktop/src/app/eidolon](apps/desktop/src/app/eidolon).
- Ejecución de agentes y configuración: [agent](agent) y [eidolon_cli](eidolon_cli).
- Mensajería y sesiones: [gateway](gateway).
- Herramientas y extensiones: [tools](tools), [plugins](plugins) y [skills](skills).
- Pruebas y dependencias: [tests](tests), [apps/desktop/package.json](apps/desktop/package.json), [pyproject.toml](pyproject.toml) y [package.json](package.json).

Los nombres internos de módulos y los identificadores de compatibilidad no son nombres comerciales. No los renombres mecánicamente al cambiar documentación o marca. Conserva también las identidades y contratos de los proveedores de terceros.

## Desarrollo y verificación

Usa un checkout o worktree desechable y un directorio de datos aislado. Revisa los manifiestos y las instrucciones del área para conocer los requisitos. Esta guía no ofrece una receta de instalación aceptada. Instala las dependencias de los workspaces JavaScript desde la raíz del repositorio, no desde una carpeta de escritorio anidada. No ejecutes un instalador remoto por una tubería al intérprete como sustituto de revisar el código que vas a probar.

El entorno conserva la variable de compatibilidad `HERMES_HOME`. Apúntala a un directorio temporal para las pruebas; no uses un perfil real ni copies credenciales a una prueba. Aislar el directorio de datos no aísla por sí solo la red, los subprocesos ni el resto del equipo. Usa un entorno saneado y aislamiento adecuado al camino que estás verificando.

Puntos de entrada definidos por el repositorio:

- Python: `scripts/run_tests.sh`. Usa este envoltorio en vez de invocar `pytest` directamente; aísla el directorio de datos y los procesos de prueba y sanea el entorno.
- Escritorio, desde la raíz: `npm run --workspace apps/desktop typecheck`, `npm run --workspace apps/desktop lint` y `npm run --workspace apps/desktop test:ui`.
- Cambios dependientes del sistema operativo: revisa `scripts/check-windows-footguns.py` y ejecuta las pruebas en el sistema correspondiente.

Estos son comandos del código, no resultados de esta actualización documental. Revisa los efectos secundarios antes de ejecutarlos. Las pruebas nativas, de instalación, actualización y extremo a extremo necesitan un plan explícito de aislamiento y limpieza; no las lances contra una instalación real ni uses pruebas unitarias como evidencia de comportamiento nativo.

Demuestra cada corrección con pruebas del fallo y del comportamiento corregido. Registra los comandos, códigos de salida, plataforma, revisión, cambios locales, alcance y pruebas omitidas o reintentadas. Distingue persistencia sintética de respuestas reales de IA, y compilación de aceptación de una aplicación instalada. Una comprobación pendiente sigue pendiente hasta verificarse.

## Convenciones

- Amplía los mecanismos existentes antes de añadir otros. Prioriza habilidades, comandos o plugins cuando una capacidad no pertenece al núcleo.
- Conserva la caché de prompts, la alternancia de roles y la estabilidad del prompt de sistema durante una conversación.
- Mantén módulos pequeños y sigue el estilo local. Usa excepciones específicas y diagnósticos sin secretos.
- Resuelve rutas mediante los mecanismos existentes; respeta los límites del sistema de archivos, los enlaces simbólicos y las reglas de escapado de cada plataforma.
- Antes de limpiar procesos, demuestra su propiedad exacta. No uses coincidencias parciales del nombre ni terminaciones masivas.
- Mantén los límites de versiones de dependencias y los archivos de bloqueo. No añadas telemetría saliente sin consentimiento explícito.
- Revisa con cuidado los cambios de permisos, autenticación, IPC, acceso a archivos, instaladores y fuentes de actualización.

## Solicitudes de cambio e informes

Mantén un cambio lógico por solicitud e incluye el problema, la intención, referencias, reproducción, implementación y pruebas. Explica qué no se ha verificado. Las capturas y los mocks aprobados no sustituyen las pruebas del camino real.

No incluyas directorios de datos, credenciales, perfiles privados ni volcados del entorno en informes o fixtures. Para posibles vulnerabilidades, sigue [SECURITY.es.md](SECURITY.es.md). Una revisión favorable no autoriza por sí sola publicar, fusionar o desplegar; las afirmaciones sobre instaladores y versiones necesitan evidencia propia.

## Licencia

Las contribuciones se realizan bajo la [licencia MIT](LICENSE). Conserva los avisos de copyright, permisos y terceros aplicables.
