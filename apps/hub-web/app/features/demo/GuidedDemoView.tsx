import Link from "next/link";

const story = [
  ["来源记录", "Codex 完成一次仓库任务", "用户要求：默认重试次数从 2 次改为 4 次，并验证超时行为。"],
  ["编码记忆", "形成一条任务经验", "在这个仓库中，重试策略由配置、退避规则和超时测试共同约束；修改后需要运行对应测试。"],
  ["演化关系", "新事实取代旧事实，但不抹掉历史", null],
  ["召回解释", "下一次相关任务接纳这条经验", "查询涉及重试和超时配置；仓库、主题与证据均匹配，因此将当前记录编入上下文。"],
] as const;

export default function GuidedDemoView() {
  return (
    <section className="view-shell demo-view">
      <header className="page-heading">
        <h1>示例演示</h1>
        <p>这是一条静态、隔离的理解路径，不读取、不写入当前记忆命名空间。</p>
      </header>

      <div className="demo-ledger">
        <header className="demo-notice">
          <strong>示例数据</strong>
          <span>仅用于解释一条经验如何进入 Memory OS，再被下一次任务召回。</span>
        </header>
        <ol className="demo-story">
          {story.map(([stage, title, body]) => (
            <li key={stage}>
              <div>
                <small>{stage}</small>
                <h2>{title}</h2>
                {body ? <p>{body}</p> : (
                  <dl className="demo-evolution">
                    <div>
                      <dt>原记录</dt>
                      <dd>默认重试 2 次 · 默认不召回</dd>
                    </div>
                    <div>
                      <dt>当前记录</dt>
                      <dd>默认重试 4 次 · 默认召回中</dd>
                    </div>
                  </dl>
                )}
              </div>
            </li>
          ))}
        </ol>
      </div>

      <div className="action-row">
        <Link className="secondary-action" href="/">
          返回真实记忆
        </Link>
        <Link className="primary-action" href="/?view=onboarding">
          接入我的历史
        </Link>
      </div>
    </section>
  );
}
