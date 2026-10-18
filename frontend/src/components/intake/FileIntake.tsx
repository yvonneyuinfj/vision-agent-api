import { Dropzone, FileRow } from './Dropzone'
import './intake.css'
import { SCENE_LINKAGE, MAIN_ACCEPT } from './sceneInput'
import type { IntakeFiles } from './useIntakeFiles'

/** 送检文件区：主输入 + 按场景联动的配套输入。新建任务页与工作台共用。
 *  scenes 是 scene 路由键（决策 52 后不再限死六键），无联动条目的场景只出主输入。 */
export default function FileIntake({
  scenes,
  intake,
  compact = false,
}: {
  scenes: readonly string[]
  intake: IntakeFiles
  compact?: boolean
}) {
  const links = scenes.flatMap((s) => {
    const link = SCENE_LINKAGE[s]
    return link ? [{ scene: s, ...link }] : []
  })

  return (
    <>
      <div className="field">
        <label className="label">
          检测图像 / 视频<span className="req">*</span>
        </label>
        <Dropzone className="mt-8" accept={MAIN_ACCEPT} onFiles={(list) => intake.addFiles('main', list)}>
          <svg width={compact ? 20 : 26} height={compact ? 20 : 26} viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.6">
            <path d="M12 15V5m0 0L8 9m4-4 4 4" />
            <path d="M4 15v3a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-3" />
          </svg>
          <span className="dz-t">拖拽图像或视频到此区域，或点击选择文件</span>
          <span className="dz-s">
            {compact ? '支持批量 · 同部位多视角或整段录像' : '支持批量上传 · 同部位多视角图像，或整段检测录像（MP4 / MOV）'}
          </span>
        </Dropzone>
      </div>
      <div className="mt-12">
        {intake.files
          .filter((f) => f.group === 'main')
          .map((item) => (
            <FileRow key={item.file.name + item.file.size} item={item} onRemove={() => intake.removeFile(item)} />
          ))}
      </div>

      {links.map((link) => (
        <div className="req-block mt-16" key={link.scene}>
          <div className="req-h">
            <span className="tag t-review">{link.tag}</span>
            <b>{link.title}</b>
            <span className="hint">{link.hint}</span>
            {link.blocks
              .filter((b) => b.btnText)
              .map((b) => (
                <Dropzone key={b.group} className="btn btn-sm" accept={b.accept} multiple={b.multiple}
                  onFiles={(list) => intake.addFiles(b.group, list)}>
                  {b.btnText}
                </Dropzone>
              ))}
          </div>

          {link.blocks.some((b) => !b.btnText) && (
            <div className="row" style={{ gap: 12 }}>
              {link.blocks
                .filter((b) => !b.btnText)
                .map((b) => (
                  <Dropzone key={b.group} className="grow" style={{ padding: compact ? 12 : 16 }} accept={b.accept} multiple={b.multiple}
                    onFiles={(list) => intake.addFiles(b.group, list)}>
                    <span className="dz-t">{b.label}</span>
                    <span className="dz-s">{b.sub}</span>
                  </Dropzone>
                ))}
            </div>
          )}

          <div className="mt-8">
            {link.blocks.flatMap((b) => intake.files.filter((f) => f.group === b.group)).map((item) => (
              <FileRow key={item.file.name + item.file.size} item={item} onRemove={() => intake.removeFile(item)} />
            ))}
          </div>
        </div>
      ))}
    </>
  )
}
