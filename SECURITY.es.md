# Política de seguridad de Eidolon

Eidolon es un proyecto independiente y experimental. El trabajo hacia una versión preliminar `v0.1.0` no acredita preparación para producción, una auditoría de seguridad ni una serie de versiones con soporte. Aquí no se establece una matriz de versiones admitidas, un programa de recompensas ni un plazo de respuesta.

Esta versión resume la política de referencia [SECURITY.md](SECURITY.md).

## Comunicar una vulnerabilidad

No se ha verificado un canal privado de Eidolon para este documento. Antes de compartir información sensible, consulta la [página de seguridad del repositorio](https://github.com/AetherMesh-AI/Eidolon/security) y busca una opción privada publicada por sus responsables. La existencia de esta política no significa que esa opción esté habilitada.

Si no existe un canal privado, abre un informe **sin información sensible** en los [issues de Eidolon](https://github.com/AetherMesh-AI/Eidolon/issues) preguntando cómo contactar en privado con una persona responsable. No incluyas instrucciones de explotación, detalles de una instalación vulnerable, conversaciones privadas, credenciales ni datos de usuarios. No envíes informes de este proyecto a contactos copiados del código original.

Una vez confirmado el canal privado, incluye:

- Descripción concisa, impacto y acceso o requisitos necesarios para un atacante.
- Revisión o compilación afectada, cambios locales, sistema operativo y versiones pertinentes.
- Reproducción mínima con datos desechables y cuentas de prueba que controles.
- Componente y límite de confianza implicados, con referencias al código cuando ayuden.
- Registros saneados, evidencia y posibles medidas de mitigación.

No pruebes instalaciones ni cuentas ajenas sin autorización. Si se filtraron credenciales, revócalas o rótalas mediante el proveedor; borrar un registro o commit no invalida el secreto expuesto.

## Modelo de confianza y límites

### El acceso al equipo tiene consecuencias

El entorno puede ejecutar comandos y usar archivos, navegador, red e integraciones. Un proceso local puede acceder a los recursos disponibles para su cuenta del sistema operativo. No consideres la aplicación una sandbox ni supongas que elegir otro agente crea un límite de seguridad del sistema.

Cuando necesites aislamiento, usa una cuenta separada, contenedor, máquina virtual o entorno remoto configurado adecuadamente. Revisa rutas expuestas, red, credenciales y backends de ejecución. Aislar el terminal no contiene automáticamente la aplicación principal, sus plugins, otras herramientas o el entorno heredado. Esta política no certifica ninguna configuración de aislamiento.

### Las aprobaciones y las instrucciones no son contención

Páginas, archivos, mensajes, resultados de herramientas y salidas de modelos pueden estar controlados por atacantes. Los prompts, diálogos de aprobación, detectores, mecanismos de ocultación de secretos y listas de permitidos pueden reducir riesgos, pero no son límites de contención del sistema operativo. No concedas privilegios amplios porque un modelo o la interfaz diga que una acción es segura.

Comunica casos concretos de ejecución involuntaria, evasión de autenticación, filtración de secretos, renderizado inseguro o acceso no previsto a archivos, junto con sus requisitos reales. No descartes un informe solo porque intervino contenido no fiable o un modelo; distingue una limitación heurística del incumplimiento de un límite exigible.

### El acceso externo requiere autorización

Cada adaptador accesible por red necesita una lista explícita de llamantes permitidos o autenticación y autorización equivalentes antes de ejecutar trabajo, resolver aprobaciones o devolver datos. Los identificadores de sesión sirven para enrutar; no son credenciales. El IPC local necesita los controles del sistema operativo, y exponerlo a otros usuarios requiere autenticación explícita. Son requisitos que deben comprobarse, no una afirmación de que todos los adaptadores hayan superado una revisión.

No expongas directamente a internet gateways o API experimentales. Usa controles de red adecuados, limita las salidas cuando proceda y aplica privilegios mínimos. Emplea instancias y credenciales separadas si los llamantes tienen diferentes niveles de confianza; un nombre de rol no impone esa separación.

### Los registros de prototipos no conceden permisos

Objetivos, equipos de ejemplo, actividad, decisiones y resultados de organización son registros de prototipos. Una aprobación local no concede permisos al entorno de ejecución y un resultado registrado no demuestra ejecución. La memoria y el conocimiento compartido entre agentes no son todavía un sistema maduro de control de acceso. No dependas de los roles o de una separación aparente de memoria para proteger información sensible.

### El almacenamiento local no garantiza privacidad

El directorio predeterminado es `~/.eidolon`; la configuración de compatibilidad puede cambiarlo. Una carpeta separada no implica cifrado, aislamiento de perfiles ni protección frente a otros procesos del mismo usuario. Los proveedores e integraciones configurados pueden recibir datos al usarlos; revisa sus permisos y tratamiento de datos antes de proporcionar material privado.

Usa datos desechables en versiones experimentales, copias independientes de seguridad y permisos adecuados para las credenciales. Revisa los diagnósticos antes de compartirlos. Las pruebas de inicio nativo y persistencia sintética no acreditan un tratamiento seguro de datos en todos los caminos.

### Las extensiones y actualizaciones exigen confianza

Plugins, habilidades que ejecutan código, servidores MCP, dependencias, instaladores y fuentes de actualización requieren revisión. Limita sus credenciales y privilegios. Fijar una versión ayuda a identificar código, pero no demuestra que sea seguro.

Usa fuentes verificadas. Un repositorio, número de versión o compilación exitosa no demuestra que exista un artefacto firmado o un proceso de instalación y actualización probado. No des por aceptadas la recuperación de actualizaciones, conservación de datos, conversación real con IA, primera transición entre agentes o limpieza de procesos porque otras pruebas hayan pasado.

## Responsabilidades de quienes contribuyen

Consulta [CONTRIBUTING.es.md](CONTRIBUTING.es.md) y [AGENTS.md](AGENTS.md). Los cambios sensibles deben explicar el límite protegido e incluir pruebas aisladas del camino real. Nunca incluyas credenciales activas, perfiles privados ni datos de usuarios con cargas de explotación en fixtures. Evita registrar secretos y comprueba la propiedad exacta de los procesos antes de limpiarlos.

## Licencia

Sigue siendo aplicable la [licencia MIT](LICENSE), incluidos los avisos existentes de copyright, permisos y ausencia de garantía. Esta política no sustituye esos términos.
