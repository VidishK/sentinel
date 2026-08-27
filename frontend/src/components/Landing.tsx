import { useEffect, useState, type ReactNode } from 'react'

interface LandingProps {
  onOpenAgent: () => void
  onLogIn: () => void
}

const NAV = [
  { id: 'product', label: 'Product' },
  { id: 'solutions', label: 'Solutions' },
  { id: 'how', label: 'How it works' },
  { id: 'resources', label: 'Resources' },
  { id: 'contact', label: 'Contact' },
  { id: 'about', label: 'About' },
] as const

function Reveal({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  const [visible, setVisible] = useState(false)
  const [node, setNode] = useState<HTMLElement | null>(null)

  useEffect(() => {
    if (!node) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setVisible(true)
      },
      { threshold: 0.18, rootMargin: '0px 0px -8% 0px' },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [node])

  return (
    <div
      ref={setNode}
      className={`reveal ${visible ? 'in-view' : ''} ${className}`.trim()}
    >
      {children}
    </div>
  )
}

export function Landing({ onOpenAgent, onLogIn }: LandingProps) {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    function onScroll() {
      setScrolled(window.scrollY > 24)
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  function go(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="landing">
      <header className={`top-nav landing-nav ${scrolled ? 'is-scrolled' : ''}`}>
        <div className="logo">
          <span className="logo-mark">S</span>
          <span className="logo-text">
            <strong>Sentinel</strong>
            <span>Data agent</span>
          </span>
        </div>
        <nav className="nav-links" aria-label="Product">
          {NAV.map((item) => (
            <button key={item.id} type="button" onClick={() => go(item.id)}>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="nav-actions">
          <button type="button" className="status-chip" onClick={onLogIn}>
            Log in
          </button>
          <button type="button" className="policy-badge" onClick={onOpenAgent}>
            Open agent
          </button>
        </div>
      </header>

      <section className="hero landing-hero">
        <p className="hero-kicker hero-anim">
          Execution engine
          <span className="live-dot" />
          <span className="live">Live</span>
        </p>
        <h1 className="hero-anim delay-1">Governed context for company data.</h1>
        <p className="hero-lede hero-anim delay-2">
          Control, privacy, and audit for AI agents — in real time.
        </p>
        <div className="hero-ctas hero-anim delay-3">
          <button type="button" className="btn btn-primary" onClick={onOpenAgent}>
            Open the agent
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => go('product')}>
            Explore the platform
          </button>
        </div>
      </section>

      <section id="product" className="landing-section">
        <Reveal>
          <p className="section-kicker">Product</p>
          <h2>Write access, without open access.</h2>
          <p className="section-lede">
            Sentinel lets an agent change company data only after the change is
            rehearsed, measured, and allowed.
          </p>
        </Reveal>
        <div className="feature-grid">
          {[
            {
              title: 'Rehearse first',
              body: 'Every write runs in a transaction, captures before and after, then rolls back until it is cleared.',
            },
            {
              title: 'Decide by blast radius',
              body: 'Small, bounded edits can proceed. Broad or destructive ones wait for a person.',
            },
            {
              title: 'Leave a trail',
              body: 'Preview, approve, and commit are recorded. Nothing lands without a history.',
            },
          ].map((card, index) => (
            <Reveal key={card.title} className={`delay-${index + 1}`}>
              <article className="feature-card">
                <h3>{card.title}</h3>
                <p>{card.body}</p>
              </article>
            </Reveal>
          ))}
        </div>
      </section>

      <section id="solutions" className="landing-section">
        <Reveal>
          <p className="section-kicker">Solutions</p>
          <h2>One agent. Many sources.</h2>
          <p className="section-lede">
            Point Sentinel at the database you already run. Each signed-in
            account has its own sources. Memory stays separate from the data
            it is allowed to touch.
          </p>
        </Reveal>
        <div className="feature-grid three">
          {['Postgres', 'CockroachDB', 'MongoDB'].map((name, index) => (
            <Reveal key={name} className={`delay-${index + 1}`}>
              <article className="feature-card quiet">
                <h3>{name}</h3>
                <p>Connect, introspect the catalog, then ask or propose a change.</p>
              </article>
            </Reveal>
          ))}
        </div>
      </section>

      <section id="how" className="landing-section">
        <Reveal>
          <p className="section-kicker">How it works</p>
          <h2>Three steps. No surprises.</h2>
        </Reveal>
        <ol className="steps">
          {[
            ['Ask', 'Chat in plain language. Sentinel reads the active source before it writes.'],
            ['See', 'Risky changes appear as a preview with the rows that would move.'],
            ['Keep', 'Approved work is logged. Instructions start on trial until they earn trust.'],
          ].map(([title, body], index) => (
            <Reveal key={title} className={`delay-${index + 1}`}>
              <li>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <div>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </div>
              </li>
            </Reveal>
          ))}
        </ol>
      </section>

      <section id="resources" className="landing-section">
        <Reveal>
          <p className="section-kicker">Resources</p>
          <h2>The live workspace.</h2>
          <p className="section-lede">
            After you sign in, chat sits beside preview, instructions, audit,
            safety, and sources.
          </p>
        </Reveal>
      </section>

      <section id="about" className="landing-section last">
        <Reveal>
          <p className="section-kicker">About</p>
          <h2>Built to prove itself.</h2>
          <p className="section-lede">
            Most tools ask you to trust the model. Sentinel shows the change,
            then asks whether it should exist.
          </p>
          <div className="hero-ctas">
            <button type="button" className="btn btn-primary" onClick={onLogIn}>
              Log in
            </button>
            <button type="button" className="btn btn-secondary" onClick={onOpenAgent}>
              Open the agent
            </button>
          </div>
        </Reveal>
      </section>

      <footer id="contact" className="landing-foot">
        <span>Sentinel</span>
        <span>Governed context for company data</span>
      </footer>
    </div>
  )
}
