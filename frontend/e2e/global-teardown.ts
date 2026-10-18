/** 全局后置：把批次 3 之后指向真工具的注册表复原（globalSetup 已临时绑到 mock）。 */
import { restoreRegistry } from './helpers/registry'

export default async function globalTeardown(): Promise<void> {
  await restoreRegistry()
}
