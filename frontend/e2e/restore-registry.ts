/** 手工复原 e2e 注册表基座（崩溃/中断后跑）：
 *  npx tsx e2e/restore-registry.ts */
import { restoreRegistry } from './helpers/registry'

await restoreRegistry()
