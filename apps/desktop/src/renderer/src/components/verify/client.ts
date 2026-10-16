/** Call POST /verify and follow the job until it finishes. */

import { errorMessage, sidecarRequest } from '../clarify/client'
import { parseVerifyJob, type VerifyJobView } from './view'

export async function verifyItems(itemIds: number[], mode: 'hash_mtime' | 'refetch'): Promise<VerifyJobView> {
  const started = await sidecarRequest('POST', '/verify', { item_ids: itemIds, mode })
  if (!started.ok) {
    throw new Error(started.error || errorMessage(started.data, '查证失败'))
  }
  const first = parseVerifyJob(started.data)
  const jobId = first.job_id
  if (!jobId) throw new Error('没有查证任务')
  if (first.state === 'done' || first.state === 'failed') return first
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const current = await sidecarRequest('GET', `/verify/${jobId}`)
    if (!current.ok) throw new Error(current.error || errorMessage(current.data, '查证进度读取失败'))
    const job = parseVerifyJob(current.data)
    if (job.state === 'done' || job.state === 'failed') return job
    await new Promise((resolve) => setTimeout(resolve, 150))
  }
  throw new Error('查证超时')
}
