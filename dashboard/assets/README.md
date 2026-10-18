# Dashboard assets

Assets are served inline from disk; the operator view does not contact a font, icon or
animation CDN and needs no Node build step.

- `fonts/`: Geist Sans variable and Geist Mono regular from `geist` **1.8.1**,
  https://github.com/vercel/geist-font, SIL Open Font License (`fonts/LICENSE.txt`).
- `icons/`: the regular SVG subset from `@phosphor-icons/core` **2.1.1**,
  https://github.com/phosphor-icons/core, MIT (`icons/LICENSE`).
- `vendor/gsap.min.js`: the unmodified GSAP core **3.15.0** distribution from `gsap`,
  https://gsap.com/. Its copyright and standard-license notice are retained in the file;
  terms: https://gsap.com/standard-license/.

Visual direction: restrained workspace composition inspired by 21st.dev and Rare UI,
fine-line detail inspired by Hairline, and a small dot pattern inspired by Dot Matrix.
Codrops, GSAP and Raylight inform the short, purposeful entrance motion. Shapes Gallery
and Flaticon were reviewed as references; their assets are not redistributed here.

`theme.css` scopes component adjustments to named Streamlit containers. `motion.js`
enhances existing content only: content remains visible with scripts disabled, motion
respects the OS setting, and observers/tweens are disposed on reinitialisation.
