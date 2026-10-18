/** E2E 样本数据：复用仓库现有冒烟样例图（docs/image，README §9 同源）。 */
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = fileURLToPath(new URL('.', import.meta.url))

export const SAMPLE_IMAGE = resolve(HERE, '../../../docs/image/class1_1.jpg')
