
## Reviewed TypeScript checks

When the fixed TypeScript execution adapter is available, `project_typecheck` consumes its exact current, independently reviewed compiler receipt, including selected inputs, effective configuration, pinned runtime, and confirmed cleanup. A project-scoped criterion verifies only that selected project; unscoped checks retain the runtime's all-selected-project coverage. Missing, stale, unsupported or different-kind receipts fail closed. Compiler success never satisfies `project_tests`. The single-recipe project adapter cannot combine tests and typecheck for one project in the same latest run. `project_build` remains unsupported until its separately reviewed output-manifest integration is available.
