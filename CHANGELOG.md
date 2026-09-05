# Changelog

All notable changes to praxis are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [7.15.0](https://github.com/devseunggwan/praxis/compare/v7.14.0...v7.15.0) (2026-09-05)


### Added

* **cmux:** idleness oracle for orphan pane triage ([#1255](https://github.com/devseunggwan/praxis/issues/1255)) ([e825d2f](https://github.com/devseunggwan/praxis/commit/e825d2f6b5ca77c685d382eae09cd3a7a63cea24))
* **hook:** extend rejected-mutation-reconsent to the dispatch surface ([#1262](https://github.com/devseunggwan/praxis/issues/1262)) ([30e95f0](https://github.com/devseunggwan/praxis/commit/30e95f0cb4b1218fdd60669b910849c7c406617e))
* **hooks:** advise on unenforced mandatory steps ([#1263](https://github.com/devseunggwan/praxis/issues/1263)) ([d1e3e33](https://github.com/devseunggwan/praxis/commit/d1e3e333654cf066fed2082ada5594a002e7504c))
* **hooks:** gate composed $ command lines ([96c0f98](https://github.com/devseunggwan/praxis/commit/96c0f98b59243c7c5c11e165723628c2800273a0))
* **hooks:** gate parens release-please rejects ([#1268](https://github.com/devseunggwan/praxis/issues/1268)) ([9ea4785](https://github.com/devseunggwan/praxis/commit/9ea4785ab3ca9ef0bb1f42fac3782e2b9c381730))


### Fixed

* **cmux-delegate:** drop $&lt;digit&gt; from the skill body ([#1269](https://github.com/devseunggwan/praxis/issues/1269)) ([a825eae](https://github.com/devseunggwan/praxis/commit/a825eae8417069c266ec1e16f6b20f3cce0686b6))
* **hooks:** classify string-shaped failed payload ([#1270](https://github.com/devseunggwan/praxis/issues/1270)) ([1cb7ca6](https://github.com/devseunggwan/praxis/commit/1cb7ca6e1a2cb2944b972d36312a5fcc8fef9b7e))
* **hooks:** cut the marker window at the last merge that ran ([#1264](https://github.com/devseunggwan/praxis/issues/1264)) ([b8785bb](https://github.com/devseunggwan/praxis/commit/b8785bbd757ec0aae62f9f33fbc57a11fa94ab99))
* **hooks:** declare the two undeclared advisory strict knobs ([#1260](https://github.com/devseunggwan/praxis/issues/1260)) ([b291ffd](https://github.com/devseunggwan/praxis/commit/b291ffdbd8ce032db47a12edf0cf16acd72b28fe))
* **hooks:** detect gh api comment writes ([#1267](https://github.com/devseunggwan/praxis/issues/1267)) ([e923b7d](https://github.com/devseunggwan/praxis/commit/e923b7de3f0b98822243c1bed053036612d3129b))
* **hooks:** resolve gate-4 visibility in the hook ([#1272](https://github.com/devseunggwan/praxis/issues/1272)) ([9a75c74](https://github.com/devseunggwan/praxis/commit/9a75c748fdea91829c8f3f108c7284e18ac64667))


### Changed

* **hooks:** state the anchor gh prerequisite ([#1266](https://github.com/devseunggwan/praxis/issues/1266)) ([36e8580](https://github.com/devseunggwan/praxis/commit/36e8580497d25bda32b763025f490c21f0b9706d))

## [7.14.0](https://github.com/devseunggwan/praxis/compare/v7.13.0...v7.14.0) (2026-09-03)


### Added

* **manifest:** emit an Agent Plugins manifest ([#1220](https://github.com/devseunggwan/praxis/issues/1220)) ([9eb6647](https://github.com/devseunggwan/praxis/commit/9eb66470ed997bbe9ce9948f7baa85d2eeb9220c))


### Fixed

* **hooks:** aggregate Stop-lane decision:block in run_group ([#1199](https://github.com/devseunggwan/praxis/issues/1199)) ([61dd495](https://github.com/devseunggwan/praxis/commit/61dd49545196e9cf3d39833e97a211859eb4ceff))
* **hooks:** decay the retrospect marker per turn ([#1249](https://github.com/devseunggwan/praxis/issues/1249)) ([7e14550](https://github.com/devseunggwan/praxis/commit/7e14550df60018e1dc00273cef01948750789304))
* **hooks:** distinguish an unscanned transcript from zero rejections ([#1234](https://github.com/devseunggwan/praxis/issues/1234)) ([ed8446a](https://github.com/devseunggwan/praxis/commit/ed8446ae619a38e07724966ad12923cd184141b1))
* **hooks:** host-aware commit deny checklist ([#1236](https://github.com/devseunggwan/praxis/issues/1236)) ([ed3f804](https://github.com/devseunggwan/praxis/commit/ed3f804b1e54fd2fdc1b6afa0b215f3cdf94b515))
* **hooks:** host-scope printed gate references ([#1248](https://github.com/devseunggwan/praxis/issues/1248)) ([eaa716d](https://github.com/devseunggwan/praxis/commit/eaa716da0859180c6b2dc206137573bc4f0218af)), closes [#1245](https://github.com/devseunggwan/praxis/issues/1245)
* **hooks:** pr-anchor-existence-gate records no fire events ([#1223](https://github.com/devseunggwan/praxis/issues/1223)) ([a86f695](https://github.com/devseunggwan/praxis/commit/a86f695aba3033ac88b855d66c1cfe4f7ef22dc1)), closes [#1213](https://github.com/devseunggwan/praxis/issues/1213)
* **hooks:** reconcile praxis_state_dir across _paths.sh and _paths.py ([#1222](https://github.com/devseunggwan/praxis/issues/1222)) ([cbdd22e](https://github.com/devseunggwan/praxis/commit/cbdd22e0c3569d9ce75afb00eda941906fe9bd54)), closes [#1215](https://github.com/devseunggwan/praxis/issues/1215)
* **hooks:** scan every dispatch group for fixed timeouts ([#1235](https://github.com/devseunggwan/praxis/issues/1235)) ([259dd18](https://github.com/devseunggwan/praxis/commit/259dd1868a96fe85f6a46defbe2b0e1376d6fd52))
* **hooks:** see posted anchors the gate was blind to ([#1254](https://github.com/devseunggwan/praxis/issues/1254)) ([a4d01ad](https://github.com/devseunggwan/praxis/commit/a4d01ad6a29e6aa68f96959e01205f87031deb2f))
* **retrospect:** resolve gate-4 repo visibility via the API ([#1242](https://github.com/devseunggwan/praxis/issues/1242)) ([8ab92c4](https://github.com/devseunggwan/praxis/commit/8ab92c41a500375c7b78840f8dd5d8a2062cae25))


### Changed

* fail the release run on a dropped commit ([#1233](https://github.com/devseunggwan/praxis/issues/1233)) ([2d55889](https://github.com/devseunggwan/praxis/commit/2d558892f25bfe7b8d0206e9bc5c9c188f15c913))
* **hooks:** dispatch PostToolUse(Bash) as one process ([#1253](https://github.com/devseunggwan/praxis/issues/1253)) ([6a45153](https://github.com/devseunggwan/praxis/commit/6a4515383639b70fecfdd82ad823978fa74a0058))
* **hooks:** extract shared _lib helpers ([#1232](https://github.com/devseunggwan/praxis/issues/1232)) ([2d86ff6](https://github.com/devseunggwan/praxis/commit/2d86ff6c7f389e4555f46907f50d10b09c3a607c))
* **hooks:** read postcompact transcript tail by seeking, not scanning ([#1224](https://github.com/devseunggwan/praxis/issues/1224)) ([0a9d318](https://github.com/devseunggwan/praxis/commit/0a9d31833659b114c7123782aa67c0265c2d08c7)), closes [#1155](https://github.com/devseunggwan/praxis/issues/1155)
* **hooks:** read the transcript tail in advisory scans ([#1251](https://github.com/devseunggwan/praxis/issues/1251)) ([cb0b888](https://github.com/devseunggwan/praxis/commit/cb0b888bb99c96d1bb67e5fd3682f050f6263978))
* **hooks:** scan the transcript incrementally in Stop gates ([#1243](https://github.com/devseunggwan/praxis/issues/1243)) ([3c287e7](https://github.com/devseunggwan/praxis/commit/3c287e7b99e7278eeee6405fc3b5b22bb5690a6b))
* **telemetry:** gzip finished days on rollover ([#1247](https://github.com/devseunggwan/praxis/issues/1247)) ([f5ed4c9](https://github.com/devseunggwan/praxis/commit/f5ed4c98a0fee2f9c941099c78e95eb060af15e1))

## [7.13.0](https://github.com/devseunggwan/praxis/compare/v7.12.0...v7.13.0) (2026-09-01)


### Added

* **ci:** derive sibling-gate enumeration from manifest ([#1142](https://github.com/devseunggwan/praxis/issues/1142)) ([de6bdfc](https://github.com/devseunggwan/praxis/commit/de6bdfc42d9fda5ef2892c622980b5a1e392233e)), closes [#1127](https://github.com/devseunggwan/praxis/issues/1127)
* **hooks:** add manifest.schema.json and validate the hook manifest ([#1202](https://github.com/devseunggwan/praxis/issues/1202)) ([7169e79](https://github.com/devseunggwan/praxis/commit/7169e793387a7d47cb6322d9c8ae9c119e1f5234))
* **hooks:** advise on unanchored comment sprawl ([#1143](https://github.com/devseunggwan/praxis/issues/1143)) ([c684bb7](https://github.com/devseunggwan/praxis/commit/c684bb7c528d4e6182f39c62ab243a994bd15a2a)), closes [#1141](https://github.com/devseunggwan/praxis/issues/1141)
* **hooks:** ask before a turn fans out past what was asked ([#1131](https://github.com/devseunggwan/praxis/issues/1131)) ([ae28d98](https://github.com/devseunggwan/praxis/commit/ae28d98956ca29929f3a04ef21f435cfd379d370))
* **hooks:** branch-name-check denies only when attested ([#1165](https://github.com/devseunggwan/praxis/issues/1165)) ([2cf8696](https://github.com/devseunggwan/praxis/commit/2cf8696fe41f40cde5bec88697294580b812dd18)), closes [#1159](https://github.com/devseunggwan/praxis/issues/1159)
* **hooks:** codex commit gate detects capability ([#1190](https://github.com/devseunggwan/praxis/issues/1190)) ([31bd3a5](https://github.com/devseunggwan/praxis/commit/31bd3a560a65d96800b02a98aaadf7172eb159de))
* **hooks:** declare component deps via requires ([#1163](https://github.com/devseunggwan/praxis/issues/1163)) ([a2edd9b](https://github.com/devseunggwan/praxis/commit/a2edd9b26d4ca0ca03bf9f780fdafc92b908b829)), closes [#1158](https://github.com/devseunggwan/praxis/issues/1158)
* **hooks:** PR-marker gates deny only when attested ([#1189](https://github.com/devseunggwan/praxis/issues/1189)) ([2b3104c](https://github.com/devseunggwan/praxis/commit/2b3104c59c884d1b7ad5d78734f9407b8f085ac6))


### Fixed

* close the Gate-4 doc gap and land the codeql bump as one commit ([#1147](https://github.com/devseunggwan/praxis/issues/1147)) ([d55dc54](https://github.com/devseunggwan/praxis/commit/d55dc54c8d81aa05d2ad4345f435a96037c14219))
* **cmux:** enumerate all windows in find_orphans ([#1129](https://github.com/devseunggwan/praxis/issues/1129)) ([132c1dd](https://github.com/devseunggwan/praxis/commit/132c1dda9a6d11df58e1ea91c51d7a831c9392ac))
* **hooks:** add korean wording variants to momentum gate ([#1136](https://github.com/devseunggwan/praxis/issues/1136)) ([e6f1e9a](https://github.com/devseunggwan/praxis/commit/e6f1e9af961ce64a533f1be4e0db1b8cec609410))
* **hooks:** bilingual advisory bodies ([#1166](https://github.com/devseunggwan/praxis/issues/1166)) ([a2f6aa1](https://github.com/devseunggwan/praxis/commit/a2f6aa1cf4d034d4232d774f0300f9b69b8a6250)), closes [#1160](https://github.com/devseunggwan/praxis/issues/1160)
* **hooks:** gate repo-less gh writes in cross-boundary-preflight ([#1149](https://github.com/devseunggwan/praxis/issues/1149)) ([66741e1](https://github.com/devseunggwan/praxis/commit/66741e1b1366b3b2d4cc081a61264f09a95e1e2d))
* **hooks:** per-member deadline for the Bash dispatch group ([#1195](https://github.com/devseunggwan/praxis/issues/1195)) ([ab1477a](https://github.com/devseunggwan/praxis/commit/ab1477aa380ce3b65dea430d29b80048d43384b7))
* **hooks:** personal-owner exemption reads env ([#1162](https://github.com/devseunggwan/praxis/issues/1162)) ([4d08faa](https://github.com/devseunggwan/praxis/commit/4d08faa168eb182980da277195848ee78b576402)), closes [#1156](https://github.com/devseunggwan/praxis/issues/1156)
* **hooks:** route scope-confirm logs and gh-label cache into documented roots ([#1205](https://github.com/devseunggwan/praxis/issues/1205)) ([5fdff21](https://github.com/devseunggwan/praxis/commit/5fdff21b77337a424684317a3741a33ad7e345d6))
* **hooks:** treat a lone grouping token as syntax, not as the command ([#1200](https://github.com/devseunggwan/praxis/issues/1200)) ([87aab96](https://github.com/devseunggwan/praxis/commit/87aab9691d57a866c00eb4a6aed0cfb53165e3c7))
* **retrospect:** gate issue rows at the stage4 approval ([#1144](https://github.com/devseunggwan/praxis/issues/1144)) ([511faca](https://github.com/devseunggwan/praxis/commit/511faca7aaca1419fe03d0eeaed8959365a032d0)), closes [#1138](https://github.com/devseunggwan/praxis/issues/1138)
* **retrospect:** gate-4 audits issue-routed public writes ([#1135](https://github.com/devseunggwan/praxis/issues/1135)) ([a7da22c](https://github.com/devseunggwan/praxis/commit/a7da22cf6e4da98538fbc7741380dac81d42d7fc)), closes [#1038](https://github.com/devseunggwan/praxis/issues/1038)
* **scripts:** close latent gaps in the manifest check gate ([#1204](https://github.com/devseunggwan/praxis/issues/1204)) ([b7ced98](https://github.com/devseunggwan/praxis/commit/b7ced98d3de0e50cd18b27e1606308d47ddcfd7b))
* **skills:** resolve symlinked $0 in CLI scripts ([c33ee6c](https://github.com/devseunggwan/praxis/commit/c33ee6c6cf706010039e8874b8fcbd3e812e2289))
* **skills:** standalone-tier correctness — arguments, plugin-root, bypass-review paths ([#1196](https://github.com/devseunggwan/praxis/issues/1196)) ([7771d53](https://github.com/devseunggwan/praxis/commit/7771d5325ac919840492f20f9c9669ef2a556931))
* **tests:** fold sub-suite skips into PRAXIS_TESTS_STRICT ([#1201](https://github.com/devseunggwan/praxis/issues/1201)) ([2268154](https://github.com/devseunggwan/praxis/commit/22681546e1cd2ce5e8580a2e4be9a1db20086d9d))
* **using-praxis:** route all 18 skills through the onboarding entry point ([#1197](https://github.com/devseunggwan/praxis/issues/1197)) ([f861a91](https://github.com/devseunggwan/praxis/commit/f861a918a05ad3b70b09da19d5e0be8132e5aa90))


### Changed

* **cmux-delegate:** make the delegation unit an issue ([#1134](https://github.com/devseunggwan/praxis/issues/1134)) ([09ba417](https://github.com/devseunggwan/praxis/commit/09ba41743faa29f72acd5657d26a2c10e16f5af4)), closes [#1133](https://github.com/devseunggwan/praxis/issues/1133)
* **cmux-delegate:** restore fire-and-forget ([#1132](https://github.com/devseunggwan/praxis/issues/1132)) ([2202f6e](https://github.com/devseunggwan/praxis/commit/2202f6e0bdba78735c84005187b62a533be79d3c))
* **cmux-delegate:** translate body prose to match the corpus language convention ([#1208](https://github.com/devseunggwan/praxis/issues/1208)) ([11f61cc](https://github.com/devseunggwan/praxis/commit/11f61ccc5dc58e94eec3980be96889eaf6e2522c))
* codeql.yml pins init and analyze as two separate `uses:` lines, and the ([d55dc54](https://github.com/devseunggwan/praxis/commit/d55dc54c8d81aa05d2ad4345f435a96037c14219))
* guard the workflow pinning discipline ([6e47d66](https://github.com/devseunggwan/praxis/commit/6e47d660aa040bf2fb8d4fdc439e30df1bad3a41))
* **hook:** record the settled-answer design menu as gap [#5](https://github.com/devseunggwan/praxis/issues/5) ([#1120](https://github.com/devseunggwan/praxis/issues/1120)) ([4e61f45](https://github.com/devseunggwan/praxis/commit/4e61f4574bd4b984fb091b44b2d21b23c7feebe9)), closes [#1119](https://github.com/devseunggwan/praxis/issues/1119)
* **hooks:** hook suitability audit + R1/R7 fixes ([#1161](https://github.com/devseunggwan/praxis/issues/1161)) ([e3806e6](https://github.com/devseunggwan/praxis/commit/e3806e6614a04472ae0400f7adf8853df9f7f4f2))
* **hooks:** normalize matcher spellings and add Edit/Write dispatch groups ([#1198](https://github.com/devseunggwan/praxis/issues/1198)) ([ed44c51](https://github.com/devseunggwan/praxis/commit/ed44c519e0cb8c9eb6e72ca6856163936db0d359))
* **hooks:** one jq spawn for the mix-check header ([#1152](https://github.com/devseunggwan/praxis/issues/1152)) ([7356556](https://github.com/devseunggwan/praxis/commit/73565564f9d6fe8588a86b0c21f9b4a6b8208b59)), closes [#1151](https://github.com/devseunggwan/praxis/issues/1151)
* **hooks:** record the guard/gate two-tier design ([#1188](https://github.com/devseunggwan/praxis/issues/1188)) ([2f6bc38](https://github.com/devseunggwan/praxis/commit/2f6bc380acfda92e62d8fdaee4ffc846c1a3cfe5))
* **hooks:** shell-append fire records and bound the silent-pass scan ([#1207](https://github.com/devseunggwan/praxis/issues/1207)) ([3d6a72f](https://github.com/devseunggwan/praxis/commit/3d6a72fa52b4609bd07f753fb2d1e4a781d60467))
* **hooks:** toolchain literals move to env ([#1164](https://github.com/devseunggwan/praxis/issues/1164)) ([20bd65a](https://github.com/devseunggwan/praxis/commit/20bd65a246690461411ab78016987653442f4774)), closes [#1157](https://github.com/devseunggwan/praxis/issues/1157)
* regenerate README hook aggregates and gate the counts ([#1203](https://github.com/devseunggwan/praxis/issues/1203)) ([dd4c4fd](https://github.com/devseunggwan/praxis/commit/dd4c4fd96519d16895c3592f5260acb85f4ebbf7))
* **retrospect:** [#1135](https://github.com/devseunggwan/praxis/issues/1135) widened Gate-4's selection to ([d55dc54](https://github.com/devseunggwan/praxis/commit/d55dc54c8d81aa05d2ad4345f435a96037c14219))
* **rules:** name claims that terminate in prose ([#1145](https://github.com/devseunggwan/praxis/issues/1145)) ([1162dd2](https://github.com/devseunggwan/praxis/commit/1162dd2128e51c15bb56bac67b5daed7e2ddf1d1)), closes [#1044](https://github.com/devseunggwan/praxis/issues/1044)
* shellcheck extensionless skill scripts ([3d457f4](https://github.com/devseunggwan/praxis/commit/3d457f4804b22066748f5badf775356c8833011e))

## [7.12.0](https://github.com/devseunggwan/praxis/compare/v7.11.0...v7.12.0) (2026-08-24)


### Added

* **hooks:** add pr-anchor-existence-gate Stop hook ([#1115](https://github.com/devseunggwan/praxis/issues/1115)) ([b1e03ac](https://github.com/devseunggwan/praxis/commit/b1e03ac6706366cfd834f15f279f3cfe4e1dce64))
* **hooks:** ask before a prod call whose premise may have dissolved ([#1052](https://github.com/devseunggwan/praxis/issues/1052)) ([ee1e1a1](https://github.com/devseunggwan/praxis/commit/ee1e1a1df7728e3d66942e5e0e9499e64de5e948))


### Fixed

* assorted gate/parse correctness nits ([1c4b54e](https://github.com/devseunggwan/praxis/commit/1c4b54e7689f453a72fec8db39dd279f09d5ff71)), closes [#1097](https://github.com/devseunggwan/praxis/issues/1097)
* **ci:** retry the apt phase, not just bound it ([#1050](https://github.com/devseunggwan/praxis/issues/1050)) ([738de04](https://github.com/devseunggwan/praxis/commit/738de049db84b71b775b3d0cf08929db86184e43))
* **cmux-delegate:** give the delegated worker a real stdin ([#1057](https://github.com/devseunggwan/praxis/issues/1057)) ([12634df](https://github.com/devseunggwan/praxis/commit/12634df80ee9f1c586f3b578859064571a7faca5)), closes [#1054](https://github.com/devseunggwan/praxis/issues/1054)
* **cmux-delegate:** make decision_gate write atomic ([#1073](https://github.com/devseunggwan/praxis/issues/1073)) ([da9cad5](https://github.com/devseunggwan/praxis/commit/da9cad558b0b182308591b0b6f05439ce90c0a8f))
* **cw:** read every sibling config dir's broker state ([2a5c0bd](https://github.com/devseunggwan/praxis/commit/2a5c0bd086c93da8f6ec591bbbbf06e8ece09a35))
* **cw:** warn on reaper version drift ([53edd02](https://github.com/devseunggwan/praxis/commit/53edd0273c6250ab8a35bb6ffbd7041d84e8d3d3))
* **hooks:** accept an approval in its final clause ([#1089](https://github.com/devseunggwan/praxis/issues/1089)) ([827dad9](https://github.com/devseunggwan/praxis/commit/827dad99b2edcf1aac1b927ae8b45e8806d2889d))
* **hooks:** block path-prefixed gh/git bypass ([fd25ef8](https://github.com/devseunggwan/praxis/commit/fd25ef8d28ea15265ec11bd8444040a64f41b067)), closes [#1092](https://github.com/devseunggwan/praxis/issues/1092)
* **hooks:** correct sciomc gate bypass instruction ([#1114](https://github.com/devseunggwan/praxis/issues/1114)) ([81648e1](https://github.com/devseunggwan/praxis/commit/81648e1a344607d71b681587e7b8435604e3d9af)), closes [#1112](https://github.com/devseunggwan/praxis/issues/1112)
* **hooks:** disqualify a title quoted both ways ([#1069](https://github.com/devseunggwan/praxis/issues/1069)) ([527f561](https://github.com/devseunggwan/praxis/commit/527f5610f452bdf3bef3766bb95f0631f1cbb71f))
* **hooks:** fall through when a launcher's impl is absent ([#1066](https://github.com/devseunggwan/praxis/issues/1066)) ([96436fb](https://github.com/devseunggwan/praxis/commit/96436fb4de89c23bc88c102d9ce1a790919d984f)), closes [#1053](https://github.com/devseunggwan/praxis/issues/1053)
* **hooks:** gate unqualified verdict restatement ([#1067](https://github.com/devseunggwan/praxis/issues/1067)) ([06b8b1f](https://github.com/devseunggwan/praxis/commit/06b8b1f7245ec16907912590c5f94baf7ddb824b))
* **hooks:** guard background poll-waiter chains ([#1068](https://github.com/devseunggwan/praxis/issues/1068)) ([20a412e](https://github.com/devseunggwan/praxis/commit/20a412e9700ea427cf1353be2f3b653d61e0de86))
* **hooks:** normalize command spec lookup key ([5a7fbd1](https://github.com/devseunggwan/praxis/commit/5a7fbd1206b166e719c50aaddd45bbe82278edc2)), closes [#1099](https://github.com/devseunggwan/praxis/issues/1099)
* **hooks:** stop advisory firing on exit-0 Bash ([c87cd96](https://github.com/devseunggwan/praxis/commit/c87cd9606ce217336cb0bfc080aae1939f2bb9e5)), closes [#1096](https://github.com/devseunggwan/praxis/issues/1096)
* **hooks:** stop counting exit-0 calls as failures ([#1071](https://github.com/devseunggwan/praxis/issues/1071)) ([7715c66](https://github.com/devseunggwan/praxis/commit/7715c66346ce190c5bf7a8c52a156601e79ffe9f))
* **hooks:** stop safe_tokenize dropping a line ([26a4e29](https://github.com/devseunggwan/praxis/commit/26a4e298de67aa487c4aca81d8cda3b0af73ac41)), closes [#1091](https://github.com/devseunggwan/praxis/issues/1091)
* **menu-tier:** close gap 3 for destructive sequences ([#1072](https://github.com/devseunggwan/praxis/issues/1072)) ([58a2afa](https://github.com/devseunggwan/praxis/commit/58a2afa65a66d67b259bb1ab1c9a0a7878c73202))
* **recover-sessions:** harden the recovery CLIs ([275bc38](https://github.com/devseunggwan/praxis/commit/275bc38380885eeb947965bd137030aa16cbd1d7)), closes [#1095](https://github.com/devseunggwan/praxis/issues/1095)
* **scripts:** align memory lint with runtime parser ([128438a](https://github.com/devseunggwan/praxis/commit/128438a655401102b96e35956da70a346b344a05)), closes [#1094](https://github.com/devseunggwan/praxis/issues/1094)
* **spec-drift:** skip Verify inside fenced blocks ([50d0c63](https://github.com/devseunggwan/praxis/commit/50d0c63283fc171917cdc003e7449e4e362a1c41)), closes [#1093](https://github.com/devseunggwan/praxis/issues/1093)


### Changed

* bump github/codeql-action/analyze from 4.37.6 to 4.37.7 ([#1080](https://github.com/devseunggwan/praxis/issues/1080)) ([95c36d8](https://github.com/devseunggwan/praxis/commit/95c36d88e2ef37699913d7364bb1d90ffb8bf5f6))
* bump github/codeql-action/init from 4.37.6 to 4.37.7 ([#1081](https://github.com/devseunggwan/praxis/issues/1081)) ([811cd53](https://github.com/devseunggwan/praxis/commit/811cd5331558f0bb7b461c80d3c52ba9bf4278ad))
* bump reviewdog/action-actionlint from 1.73.1 to 1.73.2 ([#1082](https://github.com/devseunggwan/praxis/issues/1082)) ([b03be52](https://github.com/devseunggwan/praxis/commit/b03be5238a568ff644c479b95ea10ae9650e6b2b))
* **coderabbit:** skip auto-review on release PRs ([#1048](https://github.com/devseunggwan/praxis/issues/1048)) ([3787644](https://github.com/devseunggwan/praxis/commit/3787644e9bbe871a0ef366cb91bedfabab2d8a0e)), closes [#1047](https://github.com/devseunggwan/praxis/issues/1047)
* **hooks:** bound Stop-gate transcript scans ([#1083](https://github.com/devseunggwan/praxis/issues/1083)) ([dc06931](https://github.com/devseunggwan/praxis/commit/dc069310542628e4611679aa6e9d1ce90dae8c90))
* **hooks:** drop the CMUX_DELEGATE exemption ([#1074](https://github.com/devseunggwan/praxis/issues/1074)) ([f7543fc](https://github.com/devseunggwan/praxis/commit/f7543fc3d6f43eb8584a60c6a5be82b166682ddb)), closes [#1055](https://github.com/devseunggwan/praxis/issues/1055)
* **hooks:** scan the transcript only when a gate needs it ([#1084](https://github.com/devseunggwan/praxis/issues/1084)) ([df31269](https://github.com/devseunggwan/praxis/commit/df31269f4cd3b8ac77b433479f0e11be0caab26a))
* **hooks:** throttle the cache sweep to once a day ([#1086](https://github.com/devseunggwan/praxis/issues/1086)) ([3e1ab0c](https://github.com/devseunggwan/praxis/commit/3e1ab0c7df88f92e7d8699040aaa8665f0f35117))
* rewrite README as a landing document ([#1090](https://github.com/devseunggwan/praxis/issues/1090)) ([fbfe0e3](https://github.com/devseunggwan/praxis/commit/fbfe0e3ace7de5518755f3536fa694957c7169c8))
* sync project tagline across manifests ([#1111](https://github.com/devseunggwan/praxis/issues/1111)) ([c1c13f0](https://github.com/devseunggwan/praxis/commit/c1c13f00bd30faa7da10e5ff53759b7f0996c477))

## [7.11.0](https://github.com/devseunggwan/praxis/compare/v7.10.0...v7.11.0) (2026-08-19)


### Added

* **hooks:** advise reply+resolve on PR threads ([#1040](https://github.com/devseunggwan/praxis/issues/1040)) ([238148d](https://github.com/devseunggwan/praxis/commit/238148df9c19b254386002900bf8ae8d986a6d9a))


### Fixed

* **ci:** bound apt timeouts on a stalled mirror ([#1046](https://github.com/devseunggwan/praxis/issues/1046)) ([559e3aa](https://github.com/devseunggwan/praxis/commit/559e3aa88c0831144878cce669241614422c657d)), closes [#1045](https://github.com/devseunggwan/praxis/issues/1045)

## [7.10.0](https://github.com/devseunggwan/praxis/compare/v7.9.1...v7.10.0) (2026-08-16)


### Added

* **cmux-delegate:** add a worker decision gate ([0087b84](https://github.com/devseunggwan/praxis/commit/0087b84f5f10aad44f3d29a9f8cc0b0ac62c0777))
* **cmux-delegate:** classify worker liveness ([3614663](https://github.com/devseunggwan/praxis/commit/361466355850ee4a46d9783a2454a8508c4d7744))
* **hook:** demote git-commit to advise, add ADVISE-channel arm ([#1030](https://github.com/devseunggwan/praxis/issues/1030)) ([df6cc4e](https://github.com/devseunggwan/praxis/commit/df6cc4e685002370ba6602793de7c9ed1cdaa483))
* **hook:** re-ask before a mutation the user already refused ([#1028](https://github.com/devseunggwan/praxis/issues/1028)) ([f4182c1](https://github.com/devseunggwan/praxis/commit/f4182c15662215042a9e312f73cef8b23842e28d))
* **hooks:** add Bash blast-radius ask to output-block-falsify-advisory ([#1025](https://github.com/devseunggwan/praxis/issues/1025)) ([6aa725b](https://github.com/devseunggwan/praxis/commit/6aa725b151e5332f750be200fab505d71b5b729b))
* **hooks:** advise commit decomposition from the message itself ([#977](https://github.com/devseunggwan/praxis/issues/977)) ([d65c1b2](https://github.com/devseunggwan/praxis/commit/d65c1b2f25c4c119fffe4b82d815472260c8348a))
* **hooks:** advise on foreground Bash calls declaring long timeouts ([#1026](https://github.com/devseunggwan/praxis/issues/1026)) ([83a95be](https://github.com/devseunggwan/praxis/commit/83a95bebf8c7a2e82115e642524144e06f7344a4))
* **hooks:** advisory for n=1 quantitative claims ([#969](https://github.com/devseunggwan/praxis/issues/969)) ([09181e8](https://github.com/devseunggwan/praxis/commit/09181e8b2dc21147394c9bce9ba5df84aaa9d1e1))
* **hooks:** enumerate required tokens on block ([512fd69](https://github.com/devseunggwan/praxis/commit/512fd6974c9ce166551fee5f1a36fbd271ef40e5)), closes [#941](https://github.com/devseunggwan/praxis/issues/941)
* **hooks:** gate poll-loop retries on reading the guard's own spec ([#1027](https://github.com/devseunggwan/praxis/issues/1027)) ([c4bd6dc](https://github.com/devseunggwan/praxis/commit/c4bd6dc8586a66b442136d187ee93b3eb5100d59))
* **hooks:** require a non-mutating tier in approval menus ([#966](https://github.com/devseunggwan/praxis/issues/966)) ([3efa1ed](https://github.com/devseunggwan/praxis/commit/3efa1edfa25395d90f357da78fce9607abb6d42d))
* **retrospect:** add denied-actions lane + Gate-12 ([#1029](https://github.com/devseunggwan/praxis/issues/1029)) ([80110bd](https://github.com/devseunggwan/praxis/commit/80110bdbd805a3b8255294bd9043a59066853aa7))
* **skill:** add merge-briefing procedure ([#980](https://github.com/devseunggwan/praxis/issues/980)) ([635f561](https://github.com/devseunggwan/praxis/commit/635f5610025f8351f3219f50a03bf56b6e11fc33))
* **skills:** spec-drift and a spec store outside the repo ([#1006](https://github.com/devseunggwan/praxis/issues/1006)) ([ea24d15](https://github.com/devseunggwan/praxis/commit/ea24d15389c3a1ff9f56850944d6436e615040ee))


### Fixed

* **cmux-delegate:** key the report on the workspace ([6d8d653](https://github.com/devseunggwan/praxis/commit/6d8d653d91b3bf7bd2607f33fa6143528748e625))
* **cmux-delegate:** supply the stdin column's non-TTY exemption ([#1022](https://github.com/devseunggwan/praxis/issues/1022)) ([615d27e](https://github.com/devseunggwan/praxis/commit/615d27e00be020b830e118f61177ffbe3f2146cf)), closes [#981](https://github.com/devseunggwan/praxis/issues/981)
* **codex-review-wrap:** always background the review call ([#983](https://github.com/devseunggwan/praxis/issues/983)) ([e18de75](https://github.com/devseunggwan/praxis/commit/e18de7589756ef8dbc5eceee8449a449591caec1))
* **hook:** make the anchor gate's PostToolUse findings reach the model ([#1000](https://github.com/devseunggwan/praxis/issues/1000)) ([7262740](https://github.com/devseunggwan/praxis/commit/7262740facaa0118b4f36f2cbb718a65f15541c8))
* **hooks:** bind each body flag to its own gh invocation in perf advisory ([#1015](https://github.com/devseunggwan/praxis/issues/1015)) ([2eefdfc](https://github.com/devseunggwan/praxis/commit/2eefdfce2c0cd2c15c8f7a2f4ff9a8f7fc016e68)), closes [#973](https://github.com/devseunggwan/praxis/issues/973) [#973](https://github.com/devseunggwan/praxis/issues/973)
* **hooks:** fold lines across an open quote in safe_tokenize ([#1014](https://github.com/devseunggwan/praxis/issues/1014)) ([c79d872](https://github.com/devseunggwan/praxis/commit/c79d8725bfd5ce5f9f1c0ac91d35c90e351c047b))
* **hooks:** guard session state read-modify-write ([#965](https://github.com/devseunggwan/praxis/issues/965)) ([5efc14c](https://github.com/devseunggwan/praxis/commit/5efc14c41a93253e8d1a0819fa34f32522136005))
* **hooks:** merge gates fire on non-merge commands ([#986](https://github.com/devseunggwan/praxis/issues/986)) ([8dfafdf](https://github.com/devseunggwan/praxis/commit/8dfafdf49a9a296a314c7d95b5bba221cee821fd)), closes [#985](https://github.com/devseunggwan/praxis/issues/985)
* **hooks:** serialize jq-config dedup state and stage per process ([#1017](https://github.com/devseunggwan/praxis/issues/1017)) ([0e755d1](https://github.com/devseunggwan/praxis/commit/0e755d168e94a321c7cce2b5c21c632bee8a3148)), closes [#970](https://github.com/devseunggwan/praxis/issues/970) [#951](https://github.com/devseunggwan/praxis/issues/951)
* **hooks:** stop rule 1b firing on refusal-led turns ([#976](https://github.com/devseunggwan/praxis/issues/976)) ([f3979aa](https://github.com/devseunggwan/praxis/commit/f3979aa2079768ffadd487adb83d2861fdf787c0))
* **menu-tier:** gate create/update on shared surface, cancel on mutation ([#1016](https://github.com/devseunggwan/praxis/issues/1016)) ([8a62c97](https://github.com/devseunggwan/praxis/commit/8a62c9790bda30421ed03a1892f1901e6965bccb))
* **retrospect:** escalate own-org public repo writes at Gate-4 ([#1024](https://github.com/devseunggwan/praxis/issues/1024)) ([256b428](https://github.com/devseunggwan/praxis/commit/256b4286f86528098c74f313ba88f8997dfc99b0))
* **spec-drift:** run Verify commands with stdin at /dev/null ([#1018](https://github.com/devseunggwan/praxis/issues/1018)) ([487aea8](https://github.com/devseunggwan/praxis/commit/487aea89aa9ed4047045539cc46feab33485ab13)), closes [#1008](https://github.com/devseunggwan/praxis/issues/1008)


### Changed

* **architecture:** state stdin column precondition ([1a5ea64](https://github.com/devseunggwan/praxis/commit/1a5ea6420425ec272b91e4404a1609d737b6e592))
* bump codeql-action/analyze to 4.37.6 ([ddea2e3](https://github.com/devseunggwan/praxis/commit/ddea2e3d196bbd533730090421d5315fcaf6bb1a))
* bump codeql-action/init to 4.37.6 ([e1acc9a](https://github.com/devseunggwan/praxis/commit/e1acc9a7ec43f62cd28dc9e9be6aba84eddc15f5))
* bump reviewdog/action-actionlint from 1.73.0 to 1.73.1 ([#1032](https://github.com/devseunggwan/praxis/issues/1032)) ([075ac78](https://github.com/devseunggwan/praxis/commit/075ac788bc54a7b1ed6202462a81ec2c9d230cf3))
* **codex-review-wrap:** pin Step 4b and Liveness to companion 1.0.6 ([#1020](https://github.com/devseunggwan/praxis/issues/1020)) ([cb0b375](https://github.com/devseunggwan/praxis/commit/cb0b375264564e939900ff24e93763c627b37f31))
* **ethos:** forbid delegating a hook workaround to the user ([#1023](https://github.com/devseunggwan/praxis/issues/1023)) ([ad1ecb5](https://github.com/devseunggwan/praxis/commit/ad1ecb55a6666555de3ba9e27525d4db08d334b0))
* **retrospect:** declare memory-lint CI split intended, not a gap ([#1021](https://github.com/devseunggwan/praxis/issues/1021)) ([0a096ae](https://github.com/devseunggwan/praxis/commit/0a096ae5f4743762fb51831df8ac977bf264aa29)), closes [#975](https://github.com/devseunggwan/praxis/issues/975)
* **spec:** a Verify oracle must fail when its inputs are gone ([#1019](https://github.com/devseunggwan/praxis/issues/1019)) ([f66836e](https://github.com/devseunggwan/praxis/commit/f66836ec6bbd97e6dfb94e49676d19bfd0e9aad0)), closes [#1011](https://github.com/devseunggwan/praxis/issues/1011) [#1008](https://github.com/devseunggwan/praxis/issues/1008)
* **spec:** tracked feature-spec convention under .praxis/specs ([#1002](https://github.com/devseunggwan/praxis/issues/1002)) ([7821fa8](https://github.com/devseunggwan/praxis/commit/7821fa87bb4d2f4aeb71a030349f44926cb555d3))

## [7.9.1](https://github.com/devseunggwan/praxis/compare/v7.9.0...v7.9.1) (2026-08-11)


### Fixed

* **hook:** accept the english anchor field labels ([#961](https://github.com/devseunggwan/praxis/issues/961)) ([b21adc5](https://github.com/devseunggwan/praxis/commit/b21adc52d47f40a5c30472b5251b93ac46e735f2)), closes [#960](https://github.com/devseunggwan/praxis/issues/960)

## [7.9.0](https://github.com/devseunggwan/praxis/compare/v7.8.0...v7.9.0) (2026-08-08)


### Added

* **codex-review-wrap:** add round-continuation gate ([#946](https://github.com/devseunggwan/praxis/issues/946)) ([950d934](https://github.com/devseunggwan/praxis/commit/950d934ed9c3ac74851417d3627b75da550d5f78)), closes [#945](https://github.com/devseunggwan/praxis/issues/945)
* **hook:** gate the PR verification anchor comment ([#948](https://github.com/devseunggwan/praxis/issues/948)) ([20bd73d](https://github.com/devseunggwan/praxis/commit/20bd73d62fbb4b6de7b1c731522ccb55bc1d601c))
* **hooks:** advise on repeat failures ([#950](https://github.com/devseunggwan/praxis/issues/950)) ([816903e](https://github.com/devseunggwan/praxis/commit/816903e2963add2b5bada49f7cc5fa483467c27a))
* **hooks:** gate evidence class on the changed surface ([#957](https://github.com/devseunggwan/praxis/issues/957)) ([03d4f8a](https://github.com/devseunggwan/praxis/commit/03d4f8acb30ef7c4da0f4e64cde4a0816b50e978))
* **reaper:** reclaim brokers in unowned workspaces ([#936](https://github.com/devseunggwan/praxis/issues/936)) ([8bf1dfa](https://github.com/devseunggwan/praxis/commit/8bf1dfa19c7667f54813643705377f6aca4b9209)), closes [#926](https://github.com/devseunggwan/praxis/issues/926)


### Fixed

* **bypass-review:** filter fixture sessions from fire-rate ([#953](https://github.com/devseunggwan/praxis/issues/953)) ([379f25e](https://github.com/devseunggwan/praxis/commit/379f25ec09ae4901f4a5fe086b40866d13f0be6c))
* **hooks:** stop gh api squash path falling through ([#952](https://github.com/devseunggwan/praxis/issues/952)) ([69e5a4d](https://github.com/devseunggwan/praxis/commit/69e5a4da68fdaa7e07001fc942f821b154330d9f))
* **hooks:** verify briefing before marker releases merge ([#956](https://github.com/devseunggwan/praxis/issues/956)) ([9e55a40](https://github.com/devseunggwan/praxis/commit/9e55a40d5aa24594ef88b093d981b8f5821ec5d9))

## [7.8.0](https://github.com/devseunggwan/praxis/compare/v7.7.0...v7.8.0) (2026-08-03)


### Added

* **hook:** advise on direct pytest execution ([#915](https://github.com/devseunggwan/praxis/issues/915)) ([ff3a51c](https://github.com/devseunggwan/praxis/commit/ff3a51cd20dfc17a1ffd98c494a00dc325d14ab9))
* **hook:** gate code-defect claims on call-site probe ([#913](https://github.com/devseunggwan/praxis/issues/913)) ([3ab294f](https://github.com/devseunggwan/praxis/commit/3ab294f0cc4fe6129a587e0fffd338fa3a96d7b0))
* **hook:** gate Write-surface decision blocks on internal consistency ([#912](https://github.com/devseunggwan/praxis/issues/912)) ([5d81746](https://github.com/devseunggwan/praxis/commit/5d81746f4a6c9484364e7c28b7e0d56cd49b08ae))
* **hooks:** enumerate verb gates on first block ([#931](https://github.com/devseunggwan/praxis/issues/931)) ([cf71fe9](https://github.com/devseunggwan/praxis/commit/cf71fe918afc7f6e1ac6c536e94379f7fcc59ace)), closes [#873](https://github.com/devseunggwan/praxis/issues/873)
* **retrospect:** gate remedy-reach receipt ([#930](https://github.com/devseunggwan/praxis/issues/930)) ([3b7495b](https://github.com/devseunggwan/praxis/commit/3b7495b831b588d35a21d07da7dd5758b01ab726)), closes [#917](https://github.com/devseunggwan/praxis/issues/917)


### Fixed

* **codex-review-wrap:** gate reap on owner death ([#923](https://github.com/devseunggwan/praxis/issues/923)) ([303a466](https://github.com/devseunggwan/praxis/commit/303a466dd71a78fe25a76524b605f2a1f304c390)), closes [#919](https://github.com/devseunggwan/praxis/issues/919)
* **cw:** guard --gc against live sessionDir ([#927](https://github.com/devseunggwan/praxis/issues/927)) ([1f55c99](https://github.com/devseunggwan/praxis/commit/1f55c99fb7341a5c5e8ca518a7f57cdc6f0fe8be)), closes [#921](https://github.com/devseunggwan/praxis/issues/921)
* **hook:** demote 세션 종료 to separator form ([#924](https://github.com/devseunggwan/praxis/issues/924)) ([20fc2ae](https://github.com/devseunggwan/praxis/commit/20fc2aebfda7f3264d7cc58324e9956b905919ac))
* **hook:** downgrade Recommended T1 deny back to ask ([#900](https://github.com/devseunggwan/praxis/issues/900)) ([d8d389c](https://github.com/devseunggwan/praxis/commit/d8d389c5c97f4652cd87903a8d05622358ca9e54))
* **hook:** expose exact falsified predicate ([3568a43](https://github.com/devseunggwan/praxis/commit/3568a43e272af6616575e5cf1f316ad829de4d8e)), closes [#910](https://github.com/devseunggwan/praxis/issues/910)
* **hook:** narrow negative-existence framing tokens ([#902](https://github.com/devseunggwan/praxis/issues/902)) ([46b08af](https://github.com/devseunggwan/praxis/commit/46b08afa3906a67b311dbf455acaef4da43c6d84))
* **hooks:** emit verb checklist on both channels ([#933](https://github.com/devseunggwan/praxis/issues/933)) ([e25689f](https://github.com/devseunggwan/praxis/commit/e25689f2bbb0534092979f4588dae7972b7a1846)), closes [#932](https://github.com/devseunggwan/praxis/issues/932)
* **hooks:** exempt live session from cache sweep ([#928](https://github.com/devseunggwan/praxis/issues/928)) ([87358f4](https://github.com/devseunggwan/praxis/commit/87358f4e83b25743c2dd2b6622e165bc04fc7348)), closes [#920](https://github.com/devseunggwan/praxis/issues/920)
* **telemetry:** divert dev-checkout fires off the ledger ([#935](https://github.com/devseunggwan/praxis/issues/935)) ([eb6b3f3](https://github.com/devseunggwan/praxis/commit/eb6b3f34669ff573db256dc27f47e12bbe78fbfc)), closes [#934](https://github.com/devseunggwan/praxis/issues/934)
* **tests:** isolate fire-ledger in run-tests.sh ([#925](https://github.com/devseunggwan/praxis/issues/925)) ([25be6a2](https://github.com/devseunggwan/praxis/commit/25be6a2f874c3ebbc667e5a8bd4265183d2bdab9))


### Changed

* **hook:** extract shared external-write body module ([#908](https://github.com/devseunggwan/praxis/issues/908)) ([cf9c387](https://github.com/devseunggwan/praxis/commit/cf9c387b25bee2e5eeb7ace405d56baea6ba9d93))
* **hooks:** consolidate runtime files under PRAXIS_HOME ([#911](https://github.com/devseunggwan/praxis/issues/911)) ([bfd7a61](https://github.com/devseunggwan/praxis/commit/bfd7a61cd7623ba110c48e30669a1d663374e6b3)), closes [#903](https://github.com/devseunggwan/praxis/issues/903)
* **tests:** fail loud on skipped linters ([#929](https://github.com/devseunggwan/praxis/issues/929)) ([83cb2ae](https://github.com/devseunggwan/praxis/commit/83cb2aed457dfe9f27b883c2ce5df66dd21c9942)), closes [#917](https://github.com/devseunggwan/praxis/issues/917)

## [7.7.0](https://github.com/devseunggwan/praxis/compare/v7.6.0...v7.7.0) (2026-07-28)


### Added

* **hook:** advisory for perf multiplier without timing artifact ([#888](https://github.com/devseunggwan/praxis/issues/888)) ([105a7f9](https://github.com/devseunggwan/praxis/commit/105a7f9cd0ee5a0f9dd95a53daf1dad360eeb55b)), closes [#850](https://github.com/devseunggwan/praxis/issues/850)
* **hook:** block bare git worktree prune without snapshot ([#881](https://github.com/devseunggwan/praxis/issues/881)) ([363d6c7](https://github.com/devseunggwan/praxis/commit/363d6c74b10ccd02141b9c470dab72cd85ec8150)), closes [#870](https://github.com/devseunggwan/praxis/issues/870)
* **hook:** cover negative-polarity PR state claims ([#884](https://github.com/devseunggwan/praxis/issues/884)) ([318fc0e](https://github.com/devseunggwan/praxis/commit/318fc0e6890f14b59d15c90f1e80a67724bc2fd9))
* **hook:** gate PR-claims lacking same-turn mutation ([#880](https://github.com/devseunggwan/praxis/issues/880)) ([7ba3a03](https://github.com/devseunggwan/praxis/commit/7ba3a0317e0ffb21af37d4805dfbe60f89707c55)), closes [#868](https://github.com/devseunggwan/praxis/issues/868)
* **hooks:** advisory for cwd-dependent relative execution ([#882](https://github.com/devseunggwan/praxis/issues/882)) ([726abf9](https://github.com/devseunggwan/praxis/commit/726abf95c917070ec7bf3f995328b3ec3389e7b8)), closes [#852](https://github.com/devseunggwan/praxis/issues/852)
* **hooks:** cover squash-merge title length ([#890](https://github.com/devseunggwan/praxis/issues/890)) ([a51d83b](https://github.com/devseunggwan/praxis/commit/a51d83b0b7454493127fbd90ecb69a378d96b4c9))
* **hooks:** guard force-push in bash commands ([#886](https://github.com/devseunggwan/praxis/issues/886)) ([20385bd](https://github.com/devseunggwan/praxis/commit/20385bd9e6817d66de3593ce8eba76b6b037afff))
* **hooks:** stop-lane gate for prose proposal blocks ([#885](https://github.com/devseunggwan/praxis/issues/885)) ([b97b541](https://github.com/devseunggwan/praxis/commit/b97b541c7e56398c1136a4740fbd476718d56503))
* **hook:** warn on suppressed-stderr negative-verdict fallback ([#896](https://github.com/devseunggwan/praxis/issues/896)) ([1381f4f](https://github.com/devseunggwan/praxis/commit/1381f4f54c26bfef852a1535b7b5952e49ae4ea0))
* **skills:** file-based agent report handoff ([#894](https://github.com/devseunggwan/praxis/issues/894)) ([1918eae](https://github.com/devseunggwan/praxis/commit/1918eaebcbce1c8757868e0f948fddc20bbd2334))
* **telemetry:** instrument impl.sh hooks ([#892](https://github.com/devseunggwan/praxis/issues/892)) ([c8454f4](https://github.com/devseunggwan/praxis/commit/c8454f49a9b82bb7ff0e94acc8eff922c64f4ab9))


### Fixed

* **hooks:** honor CLAUDE_CONFIG_DIR in memory resolver ([#878](https://github.com/devseunggwan/praxis/issues/878)) ([337d7c6](https://github.com/devseunggwan/praxis/commit/337d7c60ce78793769b2ba12f2eb1b571108e795)), closes [#853](https://github.com/devseunggwan/praxis/issues/853)
* **telemetry:** isolate test writes from ledger ([#883](https://github.com/devseunggwan/praxis/issues/883)) ([e4df858](https://github.com/devseunggwan/praxis/commit/e4df8582842098ffe73f9b29127dccb28a6addc3))
* **tests:** guard mktemp -d failure across the suite ([#898](https://github.com/devseunggwan/praxis/issues/898)) ([1980c6e](https://github.com/devseunggwan/praxis/commit/1980c6efd3dac115c6c6d3f360e82d214fcc2231))


### Changed

* add lint tier to run-tests.sh ([#875](https://github.com/devseunggwan/praxis/issues/875)) ([b3ab7db](https://github.com/devseunggwan/praxis/commit/b3ab7db1ab41f2971b8e1e1c9ec1591c3f0a9b40)), closes [#866](https://github.com/devseunggwan/praxis/issues/866)
* **contributing:** qualify canary lag figures ([#895](https://github.com/devseunggwan/praxis/issues/895)) ([15ae2ce](https://github.com/devseunggwan/praxis/commit/15ae2cea17ea23909abe0fa72cc0ecd15162891f))
* **worktree-merge-cleanup:** add squash merged-ness oracle ([#879](https://github.com/devseunggwan/praxis/issues/879)) ([485dc34](https://github.com/devseunggwan/praxis/commit/485dc3462112145e1301404fafef46392b67af01)), closes [#871](https://github.com/devseunggwan/praxis/issues/871)

## [7.6.0](https://github.com/devseunggwan/praxis/compare/v7.5.0...v7.6.0) (2026-07-27)


### Added

* **codex-review-wrap:** per-finding approval gate ([#863](https://github.com/devseunggwan/praxis/issues/863)) ([d742c41](https://github.com/devseunggwan/praxis/commit/d742c41d689c2ebf36613862e5968a614715f2bf))
* **hook:** add artifact-verdict evidence gate ([#864](https://github.com/devseunggwan/praxis/issues/864)) ([9ac8fa3](https://github.com/devseunggwan/praxis/commit/9ac8fa32468cb3b1b0a8a4794e77f136a29f5117)), closes [#862](https://github.com/devseunggwan/praxis/issues/862)
* **hooks:** record stop-lane block/advise fires ([#855](https://github.com/devseunggwan/praxis/issues/855)) ([cd758b9](https://github.com/devseunggwan/praxis/commit/cd758b95e9c8efd90b65b75f528eafcaabaf4ca1))


### Changed

* bump actions/checkout from 7.0.0 to 7.0.1 ([#856](https://github.com/devseunggwan/praxis/issues/856)) ([68ec063](https://github.com/devseunggwan/praxis/commit/68ec063020939ca22060321ee70971e6ddffa920))
* bump actions/setup-python from 6.3.0 to 7.0.0 ([#858](https://github.com/devseunggwan/praxis/issues/858)) ([4343a82](https://github.com/devseunggwan/praxis/commit/4343a828135ec1713ff1a82006a20278bc9185db))
* bump github/codeql-action/analyze from 4.37.1 to 4.37.3 ([#859](https://github.com/devseunggwan/praxis/issues/859)) ([1bde14d](https://github.com/devseunggwan/praxis/commit/1bde14dc01d257a864d920d93ee175057fab713d))
* bump github/codeql-action/init from 4.37.1 to 4.37.3 ([#857](https://github.com/devseunggwan/praxis/issues/857)) ([3bba3d1](https://github.com/devseunggwan/praxis/commit/3bba3d10560344c84b591f4a6e4217f899ec5942))
* bump reviewdog/action-actionlint from 1.72.0 to 1.73.0 ([#860](https://github.com/devseunggwan/praxis/issues/860)) ([0f2808e](https://github.com/devseunggwan/praxis/commit/0f2808eccab95f6da4f7b5990582f79487c41504))
* **hooks:** formalize canary verification steps ([#851](https://github.com/devseunggwan/praxis/issues/851)) ([f266f14](https://github.com/devseunggwan/praxis/commit/f266f1459bebe07ccd332b30f9b7d7fea97f0ae4))
* **worktree-merge-cleanup:** guard prune blast radius ([#867](https://github.com/devseunggwan/praxis/issues/867)) ([d689960](https://github.com/devseunggwan/praxis/commit/d689960d523c8516a69b4dea1d0742e7c84be912))

## [7.5.0](https://github.com/devseunggwan/praxis/compare/v7.4.0...v7.5.0) (2026-07-22)


### Added

* **hooks:** add pr-report-destination-gate Stop hook ([#833](https://github.com/devseunggwan/praxis/issues/833)) ([244e170](https://github.com/devseunggwan/praxis/commit/244e1700d57cdb9ea7db0714833a72e23c0a565e))
* **hooks:** add secret-print-redaction advisory ([#838](https://github.com/devseunggwan/praxis/issues/838)) ([4d4699e](https://github.com/devseunggwan/praxis/commit/4d4699e5952f5b857532d9f34fc5a9a22efaa6a9))
* **hooks:** add source-citation probe gate ([#839](https://github.com/devseunggwan/praxis/issues/839)) ([255c066](https://github.com/devseunggwan/praxis/commit/255c06661938e3e2fe05226e51ec26b29f5a3f78))
* **hooks:** surface pr-body tokens at deny time ([#840](https://github.com/devseunggwan/praxis/issues/840)) ([18bf86f](https://github.com/devseunggwan/praxis/commit/18bf86f60e700513a3e9e5f094e816b6c56873f4)), closes [#824](https://github.com/devseunggwan/praxis/issues/824)


### Fixed

* **ask-falsify-gate:** move falsified line out of question body ([0e58c46](https://github.com/devseunggwan/praxis/commit/0e58c46c77559da0e641c7d07021d1506a87c2aa))
* **hooks:** extract shared memory-dir resolver ([#837](https://github.com/devseunggwan/praxis/issues/837)) ([e56e7e1](https://github.com/devseunggwan/praxis/commit/e56e7e1efd5a7777ef09963e1d473b661728d504))
* **momentum-gate:** add in-band briefing-surfaced bypass marker ([#835](https://github.com/devseunggwan/praxis/issues/835)) ([b2bbaf3](https://github.com/devseunggwan/praxis/commit/b2bbaf3f8fb80521a1d45a73eb6e0a36afdd69c7))
* **momentum-gate:** scope merge-briefing window to prior turn ([#834](https://github.com/devseunggwan/praxis/issues/834)) ([f4319bc](https://github.com/devseunggwan/praxis/commit/f4319bcf634fc5d88f8cdd277de2f86318373f99))


### Changed

* add review-body surface to pr comment scope ([#836](https://github.com/devseunggwan/praxis/issues/836)) ([0212f68](https://github.com/devseunggwan/praxis/commit/0212f684bb5a1c8e90fe6c60fd8488a0c1b5b687))
* **ask-falsify-gate:** allow evidence in description ([#829](https://github.com/devseunggwan/praxis/issues/829)) ([0e58c46](https://github.com/devseunggwan/praxis/commit/0e58c46c77559da0e641c7d07021d1506a87c2aa))

## [7.4.0](https://github.com/devseunggwan/praxis/compare/v7.3.0...v7.4.0) (2026-07-18)


### Added

* **hook:** model-routing tier-mismatch advisory ([#789](https://github.com/devseunggwan/praxis/issues/789)) ([af98844](https://github.com/devseunggwan/praxis/commit/af988445979a6dc56fad8dae4e38d325dc9ba770))
* **hook:** pre-commit staged-file enum advisory ([#785](https://github.com/devseunggwan/praxis/issues/785)) ([6db4a8c](https://github.com/devseunggwan/praxis/commit/6db4a8c24b56722e2d101a49d3653a7bbfe6dc5c))
* **hooks:** add runtime-state-claim-gate stop hook ([#818](https://github.com/devseunggwan/praxis/issues/818)) ([a042f35](https://github.com/devseunggwan/praxis/commit/a042f350eb860bf6fdf6c5a0271e59226778083d))
* **hooks:** escalate momentum gate on merge ([#819](https://github.com/devseunggwan/praxis/issues/819)) ([65ff72c](https://github.com/devseunggwan/praxis/commit/65ff72c3c4009fa5ae0b8ebc2e1f1f8b8da0489b))
* **hooks:** escalate repeated same-session blocks ([#813](https://github.com/devseunggwan/praxis/issues/813)) ([5727666](https://github.com/devseunggwan/praxis/commit/5727666fc6b942c47e926a0f4f1185525ea22cdc))
* **hooks:** gate unprobed exclusion directives ([#814](https://github.com/devseunggwan/praxis/issues/814)) ([4140c51](https://github.com/devseunggwan/praxis/commit/4140c51214f392d3a7cc41d933d8af58d081dc9b))
* **hooks:** gh-merge-worktree-precondition gate ([#801](https://github.com/devseunggwan/praxis/issues/801)) ([b5e639f](https://github.com/devseunggwan/praxis/commit/b5e639fcb481112528bc69113b6fbc87cc75b35f))
* **hooks:** negative-existence verdict probe gate ([#812](https://github.com/devseunggwan/praxis/issues/812)) ([e4306b3](https://github.com/devseunggwan/praxis/commit/e4306b38bc5a05b4442fca72d008a03498a6d9a4))
* **hooks:** ready-to-fill falsified scaffold ([#796](https://github.com/devseunggwan/praxis/issues/796)) ([45065c5](https://github.com/devseunggwan/praxis/commit/45065c52d4958fe6c64a6d9b268e829e5e2dc8c8))
* **skill:** add surface-enumeration skill ([#782](https://github.com/devseunggwan/praxis/issues/782)) ([600b9f0](https://github.com/devseunggwan/praxis/commit/600b9f0c189727ca81c67e2a4f0eb501a6d94b8f))


### Fixed

* **hooks:** confirm label absence before blocking ([#808](https://github.com/devseunggwan/praxis/issues/808)) ([e5b0f3f](https://github.com/devseunggwan/praxis/commit/e5b0f3fea3e4c9cd06b164770c909ef42ed724fd)), closes [#803](https://github.com/devseunggwan/praxis/issues/803)
* **hooks:** detect over-claiming in falsify check ([#811](https://github.com/devseunggwan/praxis/issues/811)) ([945ce88](https://github.com/devseunggwan/praxis/commit/945ce887b02a62388e3f4c383c8c325a05570988))
* **hooks:** resolve memory-hint dir-slug mismatch ([#800](https://github.com/devseunggwan/praxis/issues/800)) ([d4cd812](https://github.com/devseunggwan/praxis/commit/d4cd812f4e427a48dd65cff70fc4768bcec72a3c))
* **hooks:** skip shell redirects in branch-name-check ([#810](https://github.com/devseunggwan/praxis/issues/810)) ([62d23bb](https://github.com/devseunggwan/praxis/commit/62d23bb3da0e3bd9f885d6a4c671b4cbae283934)), closes [#806](https://github.com/devseunggwan/praxis/issues/806)


### Changed

* bump github/codeql-action/analyze from 4.37.0 to 4.37.1 ([#820](https://github.com/devseunggwan/praxis/issues/820)) ([07301c0](https://github.com/devseunggwan/praxis/commit/07301c052b6d4f0ead7bc7e54d1fbe50e1991352))
* bump github/codeql-action/init from 4.37.0 to 4.37.1 ([#821](https://github.com/devseunggwan/praxis/issues/821)) ([a460c2a](https://github.com/devseunggwan/praxis/commit/a460c2a9f37b382752b5a0b8dd8d4b7b98165912))
* bump reviewdog/action-markdownlint from 0.27.0 to 0.28.0 ([#822](https://github.com/devseunggwan/praxis/issues/822)) ([34515af](https://github.com/devseunggwan/praxis/commit/34515af396adbe416b85b020a9c4ab9d4b79593b))
* **hook:** ask-end-option agent-facing decision ([#794](https://github.com/devseunggwan/praxis/issues/794)) ([9af49a6](https://github.com/devseunggwan/praxis/commit/9af49a6558311145fcb05dde3026b4e1b14d40dc))
* **hooks:** move falsify gate detail to specs ([#815](https://github.com/devseunggwan/praxis/issues/815)) ([e3fbd32](https://github.com/devseunggwan/praxis/commit/e3fbd32b41690a3ee1946ebdadb2b73bd0cdae50))
* **hooks:** on-demand home for merge cleanup seq ([#817](https://github.com/devseunggwan/praxis/issues/817)) ([754ef32](https://github.com/devseunggwan/praxis/commit/754ef32cce994d9656ae120b82cc5f60d4dae03d))
* **skills:** absorb surface-enum detail classes ([#816](https://github.com/devseunggwan/praxis/issues/816)) ([f3399db](https://github.com/devseunggwan/praxis/commit/f3399db6c8ead43c100724e0b5e9dc9196c61849)), closes [#792](https://github.com/devseunggwan/praxis/issues/792)

## [7.3.0](https://github.com/devseunggwan/praxis/compare/v7.2.1...v7.3.0) (2026-07-14)


### Added

* **hooks:** add foreground poll-loop guard ([#778](https://github.com/devseunggwan/praxis/issues/778)) ([5ce724a](https://github.com/devseunggwan/praxis/commit/5ce724aa49f5e9435ae5b438d5a8aa6bf393854e)), closes [#745](https://github.com/devseunggwan/praxis/issues/745)
* **retrospect:** enforce silent-pass completeness ([#773](https://github.com/devseunggwan/praxis/issues/773)) ([b34e590](https://github.com/devseunggwan/praxis/commit/b34e590ed0cf4808b6cba22fa3a5e6253325382b))
* **retrospect:** include sidechain events in corpus ([#765](https://github.com/devseunggwan/praxis/issues/765)) ([f5bc434](https://github.com/devseunggwan/praxis/commit/f5bc43414219d041ded0cfc4fb10aaa0649508a2))


### Changed

* bump github/codeql-action/analyze from 4.36.3 to 4.37.0 ([#767](https://github.com/devseunggwan/praxis/issues/767)) ([0d92efd](https://github.com/devseunggwan/praxis/commit/0d92efd2d609dc05b276166ed9a8b7e683b2ed49))
* bump github/codeql-action/init from 4.36.3 to 4.37.0 ([#768](https://github.com/devseunggwan/praxis/issues/768)) ([e93e61e](https://github.com/devseunggwan/praxis/commit/e93e61e11819a972f74412f0a57def6447bd28c8))
* bump lycheeverse/lychee-action from 2.8.0 to 2.9.0 ([#769](https://github.com/devseunggwan/praxis/issues/769)) ([2c1a90c](https://github.com/devseunggwan/praxis/commit/2c1a90c266891da19b638fdc4441a1941cf54095))
* exclude generated CHANGELOG from markdownlint ([#771](https://github.com/devseunggwan/praxis/issues/771)) ([b849cca](https://github.com/devseunggwan/praxis/commit/b849ccaa73ad16bd9337d922af4dd54cb14ed935)), closes [#770](https://github.com/devseunggwan/praxis/issues/770)
* **retrospect:** add fire-rate prune audit ([#777](https://github.com/devseunggwan/praxis/issues/777)) ([7793b32](https://github.com/devseunggwan/praxis/commit/7793b32dc14dad5147f55081aac64d1b7c1e9df4)), closes [#776](https://github.com/devseunggwan/praxis/issues/776)
* **retrospect:** codify stage 2.5 gates ([#775](https://github.com/devseunggwan/praxis/issues/775)) ([7e292c5](https://github.com/devseunggwan/praxis/commit/7e292c5f873992f96a3a399420f6e3a8eede3d88)), closes [#774](https://github.com/devseunggwan/praxis/issues/774)
* **retrospect:** fix stale memory-hint event coverage note ([#780](https://github.com/devseunggwan/praxis/issues/780)) ([3a0f44c](https://github.com/devseunggwan/praxis/commit/3a0f44cf9b2cd144ab168d4bbd6086ad10296051))

## [7.2.1](https://github.com/devseunggwan/praxis/compare/v7.2.0...v7.2.1) (2026-07-05)


### Fixed

* **ci:** guard release-please sync on tagging run ([#759](https://github.com/devseunggwan/praxis/issues/759)) ([f7f7c58](https://github.com/devseunggwan/praxis/commit/f7f7c585db3ae82f46ef5432a7f8b5f46e163e92)), closes [#757](https://github.com/devseunggwan/praxis/issues/757)
* **completion-verify:** block echo-fabricated evidence ([#762](https://github.com/devseunggwan/praxis/issues/762)) ([167c267](https://github.com/devseunggwan/praxis/commit/167c2678d86559c612f51be84711d7b3876e8d7e))


### Changed

* **ci:** bump manifests via release-please extra-files ([#764](https://github.com/devseunggwan/praxis/issues/764)) ([512d425](https://github.com/devseunggwan/praxis/commit/512d425cc597c3726486cf48e5756287b037ac1f)), closes [#761](https://github.com/devseunggwan/praxis/issues/761)

## [7.2.0](https://github.com/devseunggwan/praxis/compare/v7.1.0...v7.2.0) (2026-07-05)


### Changed

* automate releases with release-please ([#753](https://github.com/devseunggwan/praxis/issues/753)) ([6054b77](https://github.com/devseunggwan/praxis/commit/6054b77426614a57b255e8a32ebbee79dc120bfa))
* note squash-title drives release-please bump ([#756](https://github.com/devseunggwan/praxis/issues/756)) ([4c69904](https://github.com/devseunggwan/praxis/commit/4c699043cf24994a0bcd12b22b3063b765583405))

## [7.1.0] - 2026-07-03

11 PRs since 7.0.0. Minor release. Headline changes: the `debt`
deferred-decision ledger skill, completion of the `bypass-review fire-rate`
metrics left open from #710, and the 3 outcome-proxy telemetry signals scoped
out of #710/#737 for lack of a telemetry source at the time — `bypass-review
fire-rate`'s Outcome Proxy section now reports `external_write_revert_count`,
`rework_commit_count`, and `reclarification_loop_count` alongside the existing
`strike_count`. Plus a PR-state re-fetch gate for stale merge-approval menus,
a codex-review-wrap subagent-transcript fix, and the retrospect `is_error`
per-body enumeration fix.

### Added

- `debt`: new report-only skill — deferred-decision ledger unioning commit-trailer
  markers (`Not-tested:`, `Confidence: low`, `Rejected:`, `Directive:`,
  `Scope-risk:`) from `git log --grep` with tree compounding comments
  (`# [PR #N]`) from `grep`. Groups hits, tags markers with no stated revisit
  condition as `no-trigger`, and never modifies any file. (#711)
- `bypass-review`: fire-rate report completed with the three metrics left open
  from #710 — `advise_ignored_rate` (same-hook recurrence at the SAME advise
  decision, right-censored fires excluded), `bypass_count` (exact match via
  manifest `mode.bypass_env` when declared, else a session_id + hook-name
  token-subset + nearest-timestamp heuristic with an unattributed bucket), and
  a best-effort `strike_count` outcome-proxy joined via the strike-counter's
  per-session state. Adds three sections to the existing report without
  restructuring the Per-Hook Fire Counts table. (#710, PR #731)
- `pr-state-refetch-gate`: new `PreToolUse(AskUserQuestion)` hook — when a
  menu's question/header/option text co-occurs a PR number with a merge-intent
  keyword (EN merge/squash, KO 머지), it re-fetches live PR state via `gh pr
  view --json state,mergeStateStatus` and surfaces an advisory (or blocks under
  `PRAXIS_PR_STATE_REFETCH_STRICT=1`) when the PR is already MERGED or CLOSED,
  preventing a stale merge-approval question against a PR that no longer needs
  it. (#733)
- `destructive-bash-guard`: detects `git revert`, `gh pr close`, `gh issue
  reopen` command patterns and logs an `external_write_revert_count`
  outcome-proxy signal to the fire-ledger (command-pattern detection only,
  not state-reversal proof). (#737, #739)
- `bypass-review`: `rework_commit_count` outcome-proxy signal — correlates
  git commits to fire-event sessions via a `Session-Id:` commit trailer
  (exact match, manual convention) with a 15-minute timestamp-heuristic
  fallback for commits lacking the trailer, mirroring `bypass_count`'s
  exact-map + heuristic-fallback structure. Trailer coverage is
  forward-only (not retroactive); both limitations are documented in
  `OUTCOME PROXY LIMITATIONS`. (#737, #741, PR #742)
- `askuserquestion-loop-signal`: new `PostToolUse(AskUserQuestion)` hook
  appending a `reclarification_loop_count` outcome-proxy signal per call.
  Uses a coarse per-session call-count proxy (≥2, no topic clustering) to
  avoid adding fields to the shared fire-ledger record schema; the
  same-topic accuracy limitation (false positives on multi-topic sessions,
  false negatives across session/compaction boundaries) is documented in
  `OUTCOME PROXY LIMITATIONS`. Adds `record_session_fire()` to
  `hooks/_lib/_fire_ledger.py` as a RICH single-event writer for standalone
  hooks needing real `session_id` attribution outside the Bash dispatch
  group. (#737, #740, PR #743)

### Fixed

- `block-commit-without-codex-review` hook: the codex-review-wrap detection
  scan now also reads each subagent transcript
  (`<session-dir>/subagents/agent-*.jsonl`), so a
  `Skill(praxis:codex-review-wrap)` call made inside a Task/Agent-dispatched
  subagent is credited — a root-only scan was structurally blind to review
  work a subagent actually performed. (#730, PR #738)
- `retrospect`: Stage 2 now requires each `is_error` tool-result body to be
  read individually (no category inference from the tool name or a preceding
  result), with a `tool_census`/`is_error_count` cross-check — closing the
  under-enumeration gap where a single category-collapsed row passed the
  Gate-7 `is_error_enum` structural check. (#720, PR #729)

### Changed

- `docs`: CONTRIBUTING.md gains a pre-PR version-bump checklist (VERSION +
  generated manifests + a CHANGELOG section the release workflow can extract)
  (#728); an evidence-based hook prune audit (`docs/hook-prune-audit.md`)
  scoring keep/merge/drop per hook (#735); markdown tables realigned for
  MD060/MD056 (#736).

## [7.0.0] - 2026-06-28

Major release. Removes the `cmux-browser` skill, which has migrated to the
cmux repository (`manaflow-ai/cmux`) where it lives closer to the cmux app it
automates. Removing a public skill + its installed CLI is a breaking change
for consumers (`/praxis:cmux-browser` and the `~/.local/bin/cmux-browser`
command both disappear), so this is a semver major per the project's
convention for skill removals.

### Removed (BREAKING)

- `cmux-browser`: skill and its pass-through CLI wrapper removed from praxis.
  The cmux-side documentation skill is maintained in `manaflow-ai/cmux`; the
  praxis wrapper (`~/.local/bin/cmux-browser`, selector-error usage hints) is
  dropped because the cmux skill invokes the native `cmux browser` CLI
  directly. The session-management cmux-* skills (`cmux-delegate`,
  `cmux-recover-sessions`, `cmux-resume-sessions`, `cmux-save-sessions`,
  `cmux-session-manager`) are unaffected. (#726)

  > **Upgrade note:** if you installed praxis CLIs via `scripts/install.sh` on
  > a prior version, `~/.local/bin/cmux-browser` is now a dangling symlink.
  > Remove it manually (`rm ~/.local/bin/cmux-browser`); `scripts/verify-symlinks.sh`
  > will also flag it as `DANGLING`.

## [6.3.3] - 2026-06-25

Patch release. Hardens retrospect suppression-ledger handling and adds the
externalized critic re-scan audit trail.

### Added

- `retrospect`: conditional externalized critic re-scan tier after the
  MEMORY.md repeat scan, with `critic_diff:` recorded in the Stage 3
  suppression ledger whether the tier runs or is skipped (#702, PR #704)

### Fixed

- `retrospect-mix-check` Stop hook: Gate-8 now requires the `critic_diff:`
  ledger line alongside `worst_agent_failure:` and `self_adversarial:`, so
  Stage 3 cannot silently omit the conditional critic tier outcome (#702,
  PR #704)
- `retrospect`: Gate-8 self-incrimination and ledger-laundering hardening for
  suppression-ledger reports (#700, #703)

## [6.3.1] - 2026-06-16

Patch release. Closes the retrospect Stage-3 fence-omission bypass (#666).

### Fixed

- `retrospect-mix-check` Stop hook: a free-form / localized Stage 3 report that omitted the `<!-- retrospect:distribution begin -->` fence evaded all identifier checks, so the hook exited 0 and every gate (Gate-1..7, incl. the post-compaction Gate-7) silently no-op'd — "the gate exists but does not fire", one level deeper than "rule exists ≠ retrieval". The gate now anchors on a session-scoped *retrospect-active marker* (set at skill-invocation time, format-independent) and blocks on `marker AND table-shaped AND no-fence AND not-Stage-4`, so the fence can no longer be omitted to bypass the gates. Prose-only pre-Stage-3 clarification stops still pass through (#666, PR #667)

### Added

- `retrospect-active-marker` preflight hook (multi-event: `PreToolUse(Skill)` + `UserPromptSubmit`): maintains a session-scoped marker recording that a retrospect Stage 3 report is owed in the current turn, independent of the agent's output format. Foundation for the #666 gate above (#666, PR #667)

## [6.3.0] - 2026-06-11

17 PRs since 6.2.0. Minor release. Headline changes: a reachability gate for
applied-on-branch claims (#661), the `readonly-verify-deferral-gate` Stop hook
(#642), a post-compaction receipt gate for retrospect (#639), and automated
GitHub Releases from CHANGELOG (#631). Plus the #647 audit follow-ups
(stop-hook JSON signal unification, fail-open coverage, transcript-utils
consolidation) and CI supply-chain hardening.

### Added

- `merge-state-claim-gate` hook: `applied` claim kind — a reachability gate for "X is applied on branch B" claims in the final message. General state queries no longer release an applied claim; only reachability evidence (`git merge-base --is-ancestor`, a same-command `--json` state+baseRefName query, `git branch --contains`) does. Companion `external-write-falsify-check` Check 4 requires a reachability probe for same-line branch+applied claims in external write bodies (strict mode via `PRAXIS_APPLIED_CLAIM_STRICT=1`) (#656, PR #661)
- `block-personal-asset-leak` hook: second opt-in marker class — personal-repo `<owner>/<repo>` references, opt-in via `PRAXIS_PERSONAL_REPO_OWNERS` (unset keeps the existing dotfiles-only behavior). Matcher surface extended `Bash` → `Write|Edit|Bash` with lazy fail-open target-repo discrimination and a dotted-hostname guard against worktree-path false positives (#658, PR #659)
- `readonly-verify-deferral-gate` hook (Stop, advisory): detects the anti-pattern of *offering* to run a read-only verification ("should I check?", "진행할까요?") instead of just running it and pasting the result — the inverse of the sibling `completion-signal-gate` (#642)
- `retrospect`: Gate-7 post-compaction receipt gate — a session-level Stop-hook structural check that turns the Stage 2 "compaction + readable transcript" prose MUST into a machine-checkable receipt, after the salient-window default recurred in two independent sessions (#600, PR #639)
- `ci`: automated GitHub Releases from CHANGELOG — `.github/workflows/release.yml` on `v*` tag push / `workflow_dispatch` builds the release body via the new shared `scripts/extract-changelog-section.sh` (13-case fixture test suite included) (#631)
- `tests`: behavior test suite for `scripts/install.sh` / `scripts/verify-symlinks.sh` — 35 cases covering the detection/conflict branches the happy-path smoke never reaches (#647, PR #660)

### Changed

- `hooks`: Stop-hook signal mechanism unified on stdout JSON. The 3 python advisory hooks move from stderr+exit (effectively invisible to the user at exit 0) to `systemMessage` / `{decision: "block"}` JSON — per-hook block behavior unchanged, advisory visibility improved (#647, PR #657)
- `hooks`: transcript JSONL scan logic hoisted into a single `hooks/_lib/_transcript.py` SoT (public API 7 functions + 1 constant); 9 hooks converted, ~430 duplicated lines removed (#643, PR #652)
- `hooks`: `@fail_open` applied to the 9 standalone-executed hooks that lacked it; dispatch-covered vs standalone classification documented in DESIGN.md; new `check-plugin-manifests` Rule 15 invariant prevents regression (#645, PR #653)
- `retrospect`: SKILL.md split into `references/` (1,592 → 1,283 lines) — normative body retained in place, report template / worked examples moved to reference docs (#646, PR #654)
- `docs`: ARCHITECTURE.md gains an up-front "Architectural shape" section naming the 4 wiring patterns with anchor links (#648, PR #649); README prose converted to English with CLI docs synced (#637)
- `ci`: GitHub Actions pinned to commit SHAs (#633); `github/codeql-action` 3.36.2 → 4.36.2 (#635); `gitleaks/gitleaks-action` 2.3.9 → 3.0.0 (#634)

### Fixed

- `retrospect`: Stage 1.5 Signal-4 index byte threshold lowered below the observed host load budget (`PRAXIS_RETROSPECT_INDEX_BYTE_THRESHOLD` default 30720 → 24000) and an event-driven trigger added — an observed host truncation warning now fires Signal 4 regardless of the numeric thresholds (#651)
- audit LOW batch (#647, PR #655) — `session-intent` hook state write made atomic via `tempfile.mkstemp` + `os.replace` (H7); over-general bare `cmux-delegate` triggers `"delegate"`/`"new session"` narrowed to 5 compound phrases (S1); retrospect frontmatter delegate agent names qualified to `oh-my-claudecode:` canonical form (S4); advisory-nudge "Never block" INDEX.md wording corrected to state the 2 exceptions (H1)

## [6.2.0] - 2026-06-05

24 PRs since 6.1.3. Minor release. Headline changes: the single-process hook
dispatch runner (ADR-0002) collapses the `(PreToolUse, Bash)` hook group into one
process, and `block-pr-without-precommit-evidence` gains a `--body-file`
path-not-found diagnostic. Plus retrospect / falsify-gate fixes and CI hardening.

### Added

- `block-pr-without-precommit-evidence` hook: a distinct `--body-file not found` diagnostic. When `--body-file` names a path absent on disk (a relative path resolves against the hook's own cwd, not the PR worktree) and no inline body is present, the hook now emits a path-not-found message advising an absolute path instead of the misleading generic token-missing block (#608, PR #624)

### Changed

- `hooks`: single-process dispatch runner (ADR-0002). The `(PreToolUse, Bash)` hook group now executes through one `hooks/_dispatch.sh` → `_dispatch.py` process instead of N separate wrapper invocations, cutting per-Bash-call fork overhead. Each dispatched hook's behavior is unchanged. ADR (#614), runner (#615), build wiring (#616), finalize (#619), orphaned dispatch-only wrapper removal (#618, PR #620)
- `check-plugin-manifests` CI: two new invariants — opt-in wrapper byte-identity (Rule 6c) guarding `external-write-falsify-check.sh` against stale/missing drift (#605, PR #621), and docs/hook redirect-stub parity (Rule 14) requiring a byte-identical stub per hook dir and blocking orphans (#606, PR #622)
- `hooks`: shared-`_lib` consolidation — option-text collection (#601) and git-argv parsing (#597) hoisted into `_lib` for cross-hook reuse
- `skills`: `bypass-review` demoted from skill to CLI tool (no `SKILL.md`; not invocable as `/praxis:*`) (#583); in-body Triggers bullets retired in favor of frontmatter (#593); CLI script list single-sourced (#581)
- `ci`: workflows consolidated and triggers deduped (#592); reviewdog inline static-review actions added (#588)

### Fixed

- `output-block-falsify-advisory` hook: the deny/ask messages now state the line-start (`startswith`) requirement, so a `Falsified:` placed mid-line no longer triggers the same opaque ×2 block (#598, PR #623)
- `retrospect`: friction pre-scan now full-enumerates a readable transcript even when a compaction summary is present, rather than defaulting to the summary's salient narrative; the verbal-summary fallback is restricted to a genuinely unreachable transcript (#600, PR #625)
- `block-rename-sweep-survivors` hook: the survivor-scan subprocess call now has a timeout (#609)
- `hooks`: scope-confirm log routed to the host-neutral `praxis_home` state dir (#612)
- `skills`: `restore-sessions` trigger deduped (#589); `cmux-resume` Iron Law corrected (#586)
- `docs`: `codex-review-wrap` citations corrected (#610); the missing `[6.1.2]` changelog entry backfilled (#611)

### Security

- `ci`: credential persistence disabled in `actions/checkout`, so the `GITHUB_TOKEN` is no longer left in the runner git config after checkout (#596)

## [6.1.3] - 2026-06-04

### Added
- `retrospect`: 5th deterministic pre-scan lane `self_correction` — detects a mistake the agent caught and self-corrected mid-session (same intent + changed oracle/target/basis + prior result *wrong* not *errored*), the event the narrative pre-scan is most likely to self-servingly omit. Signature scan → per-candidate LLM judge → genuine self-corrections promote to friction events (`origin: self_correction`); every judged drop is recorded in a `self_correction` ledger, and the ledger fence is emitted even on the 0-friction early-exit path. The fence lives outside the `retrospect:distribution` boundary, so the Stop-hook parser is unaffected (#576)
- `retrospect`: three honest-labeling improvements rooted in "form-compliance crowding out intent" — (1) Stage 2 pre-agent artifact probe (read a directly-readable artifact before the MANDATORY tracer call and fold the confirmed fact into the briefing, or record `probe skipped:`), (2) per-finding behavioral-label falsification (a dual-nature finding narrowed to a lone `behavioral` label must carry a `behavioral-label-justify:` line or list all categories), and (3) a Stage 1.5 carry-forward `probe-unrunnable` branch (retain-by-default + bounded-drop escape via `PRAXIS_RETROSPECT_UNRUNNABLE_DROP_CYCLES`, default 3). Prose-only; Stop-hook parser unaffected (#577)

### Fixed
- `block-sciomc-finding-commit` hook: the finding-marker scan now parses the transcript **role-aware** (JSONL per-entry) instead of grepping the raw tail as text. The marker corpus is restricted to assistant message `text` blocks and `Agent`/`Task` subagent tool-results; markers inside user turns, system-reminder blocks, or `Read`/`Skill` tool-results that merely *load* a SKILL.md documenting the token schema no longer self-trip the gate. The consensus-refetch check still scans the full ordered stream after the finding, so a `gh pr view … --json body` recorded as an assistant Bash tool_use still satisfies the gate (#573, PR #575)
- `retrospect`: Stage 1.5 hygiene cursor now guards against multi-session lost-update. A new **Cursor write mandate** re-reads the on-disk `.omc/state/retrospect-hygiene-cursor.json` before persisting and, when a sibling session advanced the cursor since this session's entry read, union-merges the `note` carry-forward + scan trail + batch pointer instead of plain-overwriting. Previously two interleaved `/retrospect` runs clobbered each other's findings — the second session's Write silently dropped the first session's carry-forward, breaking the Stage 1.5 carry-forward guarantee under concurrency (#568)

## [6.1.2] - 2026-06-02

2 PRs since 6.1.1. Fix-only release — no new hooks or skills.

### Fixed
- `retrospect`: Stage 1.5 hygiene cursor now guards against multi-session lost-update. A new **Cursor write mandate** re-reads the on-disk `.omc/state/retrospect-hygiene-cursor.json` before persisting and, when a sibling session advanced the cursor since this session's entry read, union-merges the `note` carry-forward + scan trail + batch pointer instead of plain-overwriting. Previously two interleaved `/retrospect` runs clobbered each other's findings — the second session's Write silently dropped the first session's carry-forward, breaking the Stage 1.5 carry-forward guarantee under concurrency (#568, PR #569)
- `commit-title-format-check` hook: `release:` prefix now whitelisted for `gh pr create` titles only. The dev→prod release PR convention `release: Production Deploy (YYYY-MM-DD)` was blocked because `release` is not a Conventional Commits type and the pattern enforces a lowercase description. The whitelist is scoped to `gh pr create` — `git commit -m "release: ..."` and `gh issue create --title "release: ..."` still block (Conventional Commits enforced) (#570, PR #571)

## [6.1.1] - 2026-06-02

### Added
- `block-personal-asset-leak` hook: PreToolUse(Bash) advisory that scans `gh issue/pr create|comment|edit|review` bodies for an absolute home-dotfiles path (`/Users/<name>/.claude/...`, `/home/<name>/.config/...`) and nudges to use the portable `~/` form or remove it — a deterministic backstop for the literal personal-asset path-leak form (semantic surfacing, MCP writes, reverse-direction, tilde, and `/projects/` worktree paths are out of scope). Every body flag is scanned, relative `--body-file` resolves against the payload cwd, and `--body "$BODY"` heredoc-variable bodies are resolved before the scan. Advisory by default; `PRAXIS_PERSONAL_LEAK_STRICT=1` escalates to a block (#565)

### Changed
- `merge-menu-review-options-advisory` hook: context-aware reviewer routing (L2) — when a merge-decision menu lacks a review option, the advisory now tailors which reviewer it recommends to the change's nature (security > data > design > ux priority) by reading the branch diff, with nearest-fork-point base resolution so routing is correct on multi-base repos. Fail-open to the static generic levers; subprocess budget capped under the manifest timeout (#564)

## [6.1.0] - 2026-06-01

### Added
- `merge-state-claim-gate` hook: Stop advisory when the final assistant message asserts a completed merge/PR/issue/worktree state (EN/KR) but no fresh `gh pr|issue view/list/merge` or GitHub-MCP pull_request/issue read appears in the recent transcript — escalates the repeatedly-hallucinated merge-state-claim family from memory to a structural gate. Fail-open, `PRAXIS_MERGE_CLAIM_BYPASS` / `PRAXIS_MERGE_CLAIM_STRICT` (#503)
- `push-remote-ref-verify` hook: PostToolUse(Bash) advisory after `git push` when the remote branch tip did not advance to the pushed SHA — guards the rotating-endpoint silent-divergence failure where a second push in a session reaches a different proxy endpoint, prints `* [new branch]`, exits 0, but never lands on the intended remote. Fail-open, `PRAXIS_PUSH_VERIFY_BYPASS` / `PRAXIS_PUSH_VERIFY_STRICT` (#539)
- `pre-output-falsification-gate` hook: advisory when an AskUserQuestion (Recommended)/evaluative option is surfaced under recent negative evidence without a disconfirming probe phrase in the question body, and when a read-only status command (status/get/list) repeats ≥3× in a session (#487)
- `retrospect`: Stage 2 multi-oracle completeness gate (Gate-6) + Stage 1.5 oracle-annotation signal 5 — stored-value falsification requires same-oracle confirmation; different-oracle results emit a separate cohort-shift finding (#489)

### Changed
- `docs`: added a single hook environment-variable registry (`docs/bypass-vars.md`) cataloguing all `PRAXIS_*` / `CLAUDE_HOOK_BYPASS_*` vars by kind (opt-out / strict / config / path-test) with their owning hook, plus a **Guard Parser Boundary** section in `SECURITY.md` documenting that the token-based guards do not decode interpreter strings (`eval`/`bash -c`/`sh -c`/`python -c`/`find -exec`) — explicit threat-model boundary instead of a regex arms race (partial of #500; long-term parser-fragility tracked separately)
- `hooks`: durable cross-session state now defaults to the host-neutral `~/.praxis/state` instead of the Claude-nested `~/.claude/state/praxis` (strike counter, phantom-path markers, and the strike state read by `postcompact-context`). `PRAXIS_STATE_DIR` still overrides the base (back-compat); strike-counter migrates existing state across once on first run, and the readers fall back to the legacy location. New `_paths` helpers (`praxis_state_dir`/`praxis_cache_dir`/`legacy_state_dir`) + layout doc `docs/runtime-state-layout.md`. Volatile `${TMPDIR}/praxis-*` caches are swept in a follow-up (partial of #527)
- `hooks`: swept the remaining 20 `_main_inner`/`main()` hand-rolled fail-open wrappers onto the shared `@fail_open` decorator (`hooks/_lib/_hook_runtime.py`), so all blocking/advisory Python hooks now use a single fail-open pattern. No behavior change; each hook's copied fail-open test block is replaced by a `main.__wrapped__` structural assertion (behaviour is verified once in `tests/test_hook_runtime.sh`) (#526)

## [6.0.3] - 2026-05-29

1 PR since 6.0.2. Hook false-positive fix only — no skill or hook-behavior additions.

### Fixed
- `hook`: `pre-edit-protected-branch-guard` now skips gitignored paths (`git check-ignore`). Gitignored files (runtime state under `.omc/state/`, build artifacts, caches) can never be committed/PR'd, so the Issue-Driven Worktree Workflow the guard enforces is categorically inapplicable — blocking them was a false positive. `.omc/plans/` was already exempt but the sibling `.omc/state/` was not; the `check-ignore` rule generalizes beyond hardcoded paths (#493)

## [6.0.2] - 2026-05-29

1 PR since 6.0.1. Packaging fix only — no skill or hook behavior changes.

### Fixed
- `manifest`: the generated Claude `plugin.json` declared only `skills`, so Claude Code registered no hooks and the entire suite stayed dormant while skills loaded. Added the missing `hooks` field (`cursor`/`opencode` already had it; regression from the ADR-0001 Phase 2 layout move) (#491)

## [6.0.1] - 2026-05-29

12 PRs since 6.0.0. All additive (new always-on/advisory hooks, session-management refinements) or internal (refactor, docs) — no breaking changes, no removed skills or hooks.

### Added
- `inject-post-compact-session-context` hook: re-injects session context after a context-compaction event (#482)
- destructive-bash-command guard hook (#478)
- sensitive-credential-file write guard hook (#477)
- advisory nudge for `&&`-chained inspection commands (#476)
- `cmux-resume`: hostname mismatch gate (#474)
- `cmux-delegate`: handoff synthesis (#462)
- skill-surface freeze gate (`scripts/`) (#473)

### Changed
- `recover-sessions`: summary-based display name (#475)
- `hooks`: extract shared emit-decision helper (#471)

### Fixed
- `retrospect`: mandate cursor read + carry-forward in Stage 1.5 (#485)

### Docs
- `retrospect`: clarify rule-violation boundary for `dismissed_candidates` (#484); specify Stage 2.7 scope window for trigger detection (#483)

## [6.0.0] - 2026-05-27

19 PRs since 5.2.0. The headline is the ADR-0001 hook-layout migration (phases 1–3): every hook moved into a role-dir collocation layout (`hooks/<role>/<name>/{impl,spec}`) with manifest-driven generation of the per-platform `hooks.json`. On top of that, several new always-on enforcement gates (commit-title / branch-name / codex-review-on-commit / worktree-edit / child-repo-issue) and the bypass-telemetry suite (Phase 1 hook + Phase 2 review CLI) change commit/PR/write-time behavior for all users — semver **major** for the structural reorganization and the new gating surface.

### Added
- `block-commit-without-codex-review` hook (PreToolUse(Bash), claude-host): hard-blocks content `git commit` when `praxis:codex-review-wrap` has not been invoked this session; escape via `[skip-codex-review]` token or `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1` (#425, #426)
- `bulk-write-memory-checkpoint` advisory hook (PreToolUse(Edit|Write|NotebookEdit)): nudges a memory re-read on the 2nd write to a SoT path (vault/, wiki/, .claude/, skills/, AGENTS.md/CLAUDE.md); always exits 0, bypass via `PRAXIS_BULK_WRITE_BYPASS=1` (#443)
- `bypass-review` telemetry review CLI (Phase 2): read-only aggregation over the bypass-telemetry JSONL — groups by tool, surfaces most-bypassed rules, highlights bypass-then-error events (#456)
- `bypass-telemetry` PostToolUse hook (Phase 1): records bypass env-var usage to `~/.praxis/telemetry/` (names only, values redacted) (#454)
- `skill-gate` hook for external commands (#453)
- `worktree-edit-gate` hook (#452)
- child-repo issue-creation block hook (#451)
- branch-naming convention enforcement hook (#450)
- commit-title format enforcement hook (#449)
- autonomy-vs-convention doc template (#447)

### Changed
- ADR-0001 hook-layout migration: phase 1 test/wrapper prep (#424), phase 2 role layout + manifest (#432), phase 3 spec collocation (#435)
- standardized hook block-message format (#444)
- `retrospect`: 0-friction audit-trail enforcement (#446)

### Fixed
- `block-commit-without-codex-review`: harden the command parser — close grouped (`(git …)`), command-substitution (`$(git …)`), and separator-chained (`true;git …`) bypasses (#455)
- `block-sciomc-finding-commit`: harden finding detection (#448) and close commit bypasses (#445)

### Docs
- recorded the ADR-0001 phase 3 merge in `docs/adr/0001` §7 (#442)

## [5.2.0] - 2026-05-26

18 PRs since 5.1.0 — 9 feat plus fixes, refactors, tests, and docs. All additive — semver minor.

### Added
- `block-pr-without-precommit-evidence` hook (PreToolUse(Bash)): blocks PR creation when no pre-commit verification evidence exists in the session (#414)
- completion-signal retrieval gate hook: forces rule retrieval when a completion signal is emitted (#399)
- `gh --json` PreToolUse validator hook + accompanying test suite (#397 #410)
- label-existence verifier hook: confirms `gh pr`/`gh issue` label values actually exist in the target repo before the call (#388)
- `cross-check-hook-index-and-hosts` script: cross-validates the hook index against per-hook `hosts` classification (#416)
- `retrospect`: path-probe gate on the write path (#398); size-threshold signal added to the Stage 1.5 hygiene pass (#390)
- `codex-review-wrap`: Source-of-Truth (SoT) audit step (#396)
- Recommended-marker tier upgraded from `ask` to `deny` (#394)

### Changed
- `gh-json-validator`: bypass env-var naming aligned with sibling hooks (#411)
- dup-search gate: extract the search topic before running the overlap match (#389)
- rule 2 scope narrowed/namespaced to the praxis cwd (#409)

### Fixed
- Removed the redundant `hosts` array from all-host hooks (#408 #417)

### Docs
- Corrected `Supported hosts` to `claude, codex` for two gated hooks (#418)
- Indexed 7 previously-missing hooks in ARCHITECTURE (#407 #415)

## [5.1.0] - 2026-05-21

2 PreToolUse(Bash) blocking hooks from the Hub #2242 retrospect — additive, semver minor.

### Added
- `block-sciomc-finding-commit` hook (PreToolUse(Bash)): blocks `git commit` (not amend/merge/revert/cherry-pick/--allow-empty) when transcript tail contains sciomc/reviewer finding markers (`sibling-deviant`, `Stage N analysis/finding/complete`, `[FINDING:`, `[STAGE_COMPLETE:`, `scientist-agent`, `deep-dive`, `cross-validation`, `의미 mismatch`) AND no `gh pr|issue view ... --json body` or explicit ratification token was emitted AFTER the most recent finding. Escape hatches: `[user-approved]`/`[ratified-by-user]` token in commit message, `CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1` env var. Backs the "User-stated design is RATIFIED; AI analysis findings are DRAFTS" rule (#374 #383)
- `block-gh-issue-create-without-dup-search` hook (PreToolUse(Bash)): blocks `gh issue create` when no prior `gh search issues` / `gh issue list` / `gh issue view` exists in the same session transcript, OR when prior searches exist but no extracted keyword from `--title` overlaps with any prior search args. Escape hatches: `[dup-checked]`/`[no-search-needed]` token in title, personal-repo carve-out (`--repo devseunggwan/*`), `CLAUDE_HOOK_BYPASS_DUP_GATE=1` env var. Backs CLAUDE.md "GitHub Issue Hygiene" (#374 #383)

## [5.0.0] - 2026-05-21

3 hook removals labeled BREAKING force a semver major bump; also 2 feat + a repo-wide identifier sweep.

### Added
- `block-manufactured-action-menu` hook: affirmative-form option-label markers (`그대로 진행`, `execute now`, `as instructed`) extend the question-form set so a clarification menu surfaced after an explicit directive is caught from the option-label side; `execute` / `run it` / `implement` added to the command-signal set as the directives that pair with them (#377 #379)
- `retrospect`: Stage 4 Action 6 (hook code) creates a dedicated worktree when the hook target repo is on a protected branch, so the inline write is not blocked by `pre-edit-protected-branch-guard` (#375 #380)

### Removed (BREAKING)
- `trino-describe-first` hook + paired `-pre`/`-post` shims + spec + tests (Trino MCP-specific gate; not generic enough for upstream praxis)
- `trino-catalog-gate` hook + paired `-post` shim + spec + tests (Trino MCP-specific catalog gate)
- `cross-repo-worktree-preflight` hook + shim + spec + sibling test (org-specific worktree mismatch detector)

### Changed
- Repo-wide identifier sweep: `laplace-*` / `hubctl` / `windmill` / `signoz` / `channeltalk` / `airflow` / `laplacetec/` removed from hook code, docs, SKILL.md examples, and test fixtures
- `cross-boundary-preflight` advisory text: internal-identifier example list genericized
- `tests/test_retrospect_routing.sh`: `PRAXIS_RETROSPECT_FORBIDDEN_PATTERNS` env var lets forks extend the banned list without forking the test (#376)

## [4.1.0] - 2026-05-21

5 feat + 1 refactor + 1 fix + 1 docs accumulated since 4.0.0. All additive — semver minor.

### Added
- `output-block-falsify-advisory` hook: T2 confidence-anchoring framing detection — scans option `label` OR `description` for EN tokens (`safer`/`safest`/`clearly`/`natural fit|choice`/`obvious choice`/`default to|choice`/`prefer this`/bare `recommend(?:ed|s)?`) and KO substrings (`안전한`/`가장 안전`/`자연스러운`/`당연히`/`분명히`/`추천`/`기본값`) alongside the existing literal `(Recommended)` / `(추천)` marker check; same `Falsified:` line satisfaction; emits distinct `ANCHORING_ASK_MSG` so downstream parsers can distinguish which tier escalated; description-only `(Recommended)` and lowercase `(recommended)` now ask-escalate (intentional upgrade) (#369 #371)
- `memory-hint` hook: event coverage extended from `PreToolUse(Bash)` only to `Bash | Edit | Write | NotebookEdit | AskUserQuestion`; per-memory `hookEvents` frontmatter opt-in (default `[Bash]` preserves prior behavior); ASCII-keyword split pattern in mixed Hangul/ASCII text (#358 #361)
- `retrospect`: Stage 1.5 hygiene + Stage 2.7 audit pass (#365 #366); pre-scan checklist + per-finding ledger (#363 #370); hookable contract integration with `memory-hint` (#356 #360)

### Changed
- `momentum-rule-retrieval-gate` hook: dynamic memory load via `momentum: [merge|dispatch|force-push]` frontmatter on individual memory files; hardcoded memory cites removed in favor of trigger-family-based opt-in; static force-push fallback retained so empty memory dir still emits the actionable rule line (#359 #362)

### Fixed
- `retrospect` Action 3: symlinked global `~/.claude/CLAUDE.md` targets are now detected via `realpath` and routed through the staging file → `AskUserQuestion` 3-option (apply / 수정 / 보류) approval path; project-local `AGENTS.md` continues to use direct `Edit` (#367)

### Docs
- `docs/hook/memory-hint.md` cross-linked from `retrospect` SKILL.md Stage 4 Action 1 so reviewers see the hookable contract at the memory-write call site (#368)

## [4.0.0] - 2026-05-18

Milestone release: 4 new PreToolUse/PostToolUse hooks, codex-review-wrap critic pre-lock probe gate, retrospect Gate-5 mandate, and 4-round codex review refinements on hook batch (#347 #348 #349 #350 #351). User-directed major bump (no breaking API changes; cumulative additions since 3.17.0 warrant milestone marker).

### Added
- `bash-worktree-existence-advisory` hook: pre-Bash advisory for `cd`/`pushd`/`(cd ...)` to missing worktree paths; heredoc fused-token forms, pushd ±N stack index, subshell-local cwd tracking, trailing `)` strip (#322 #337 #347)
- `trino-catalog-gate` hook: PreToolUse 3-part SQL catalog gate (`catalog.schema.table`); items 1-3, 6-9 refinements + dead constant removal (#321 #336 #350)
- `external-write-path-existence-check` hook: advisory for `gh issue/pr` body files referencing repo paths that do not exist on disk; inline-code path extraction, `_is_phantom` os.sep prefix fix, first-token split, `#fragment`/`?query` strip, fenced-block guard (#324 #335 #348)
- `jq-config-empty-dict-advisory` hook: advisory for jq commands targeting empty/missing config dicts; `-n`/`--null-input` multi-path handling, `_SUBST_NULL_INPUT_RE` operand-aware scrub, combined-short flag `-rn`/`-nr`, token boundary lookbehind/lookahead, broken-symlink lexists (#338 #351)
- `momentum-rule-retrieval-gate` hook: pre-dispatch/pre-merge momentum gate (#326)
- `version-bump-evidence-check` hook: changelog evidence requirement before posting external version-bump issues/PRs (#327)
- `codex-review-wrap`: Step 5f spec refinements (#339); Step 5g critic pre-lock probe gate with negative-claim enumeration + probe citation format + worked examples F1/F2 (#346 #349); diminishing-returns advisory at N rounds; grep exit=1 vs exit=2 error-table clarification
- `retrospect`: Gate-5 mandate for step 7 scan (#325); gate-4 verdict wire to mix-check (#317); falsify-before-recommended label check (#233)
- `Issue & PR Conventions` section in CLAUDE.md: partial-scope PR `Refs #N` vs full-scope `Closes #N` (#352)

### Changed
- `pre-edit-protected-branch-guard` hook: detect PR-workflow repo via recent commit `(#N)` suffix signal before write-protect (#239)
- `external-write-falsify-check` hook: structural tokenization migration

### Fixed
- `bash-worktree-existence-advisory`: subshell cwd leak, spaced subshell form, pushd cwd leak (R1-R4 codex review fixups under #347)
- `external-write-path-existence-check`: fenced-block PR body sample false-positives, NUL-binary detection, lstrip `./` quirk (R1-R4 codex review fixups under #348)
- `jq-config-empty-dict-advisory`: `--arg name -n` value-operand handling, `_scan_subst_for_config_paths` -n missing path (R1-R4 codex review fixups under #351)
- `trino-catalog-gate`: `_CATALOG_REF_NC` unused constant removed (#350 codex round 1)

### Docs
- `CLAUDE.md` / `AGENTS.md` disambig: project vs global references in `docs/hook/` and skills (#334)

## [3.17.0] - 2026-05-16

### Added
- `pre-edit-md-escape-advisory` hook: warns on Edit of `.md` files with escape-sensitive tokens without a prior Read (#238)
- `output-block-falsify-advisory` hook: nudges output-block falsification gate before surfacing `(Recommended)` options (#225)
- `pre-gh-pr-create-dedup-gate` hook: runs `gh pr list --search` before `gh pr create` to surface duplicates (#240)
- `advisory-wrapper-signature-verify` hook: warns before writing wrapper code with delegation patterns (#243)
- `block-manufactured-action-menu` hook: warns when AskUserQuestion surfaces a proceed-menu after a command-intent signal (#244)
- Shared compound-Bash cascade advisory across all block hooks (#244)
- `retrospect`: falsify-before-recommended-label check (#233)

### Changed
- `pre-edit-protected-branch-guard` hook: detect PR-workflow repo before protecting write (#239)

### Fixed
- `block-ask-end-option` hook: bare Korean end-tokens in option labels (#241)
- `codex-review-wrap`: forbid `Skill("codex:review")` probe in Step 4 (#242)
- `block-pr-without-caller-evidence` hook: reads body-file for caller evidence (#226)
- `builtin-task-postuse` hook: scope task-postuse counter per call (#223)

## [3.16.0] - 2026-05-13

### Added
- `block-manufactured-action-menu` hook: block AskUserQuestion proceed-menus after command-intent (#215)
- `external-api-literal-trigger` hook: advisory for ALL_CAPS enum candidates and 3-part SQL identifiers without prior retrieval (#216)

## [3.15.0] - 2026-05-13

### Added
- `block-ask-end-option` hook: detects indirect session-end phrasing (#213)
- `RUNTIME_CONSTRAINTS.md`: runtime constraints gate for skill authoring (#212)
- `retrospect`: tool output completeness gate (#211)

## [3.14.0] - 2026-05-12

### Added
- `pre-edit-protected-branch-guard` hook: block Edit/Write on protected branches when dirty or after PR-workflow commit (#204)

## [3.13.0] - 2026-05-12

### Added
- `cross-boundary-preflight` hook: block heredoc in `gh pr/issue create`; checklist on cross-repo `--repo` writes (#205)

## [3.12.0] - 2026-05-12

### Added
- `external-write-falsify-check` hook: author-exempt detection for unverified identifiers in mapping tables (#207)
- `codex-review-wrap`: sibling-defect cross-check step (#203)

## [3.11.0] - 2026-05-12

### Added
- `verify-commit-flag-override` hook: deny `git commit` with hook/signing override flags (#194)
- `retrospect`: backing-repo gate and recommended-label red flag (#206)

### Changed
- Hook specs split into individual `docs/hook/*.md` files (#196)

## [3.10.0] - 2026-05-11

### Added
- `trino-describe-first` hook: require `DESCRIBE <table>` before Trino MCP query references (#189)
- `block-ask-end-option` hook: warn on mechanically surfaced end options in AskUserQuestion (#193)

## [3.9.0] - 2026-05-11

### Added
- `session-intent` hook: session-scope intent-pivot gate for `gh` mutating commands (#190)

## [3.8.0] - 2026-05-11

### Added
- `gh-flag-verify` hook: validate `gh` CLI flag-subcommand combinations (#191)

## [3.7.0] - 2026-05-11

### Added
- `pre-merge-approval-gate` hook: surface per-PR approval prompt for `gh pr merge` in direct sessions (#187)

## [3.6.0] - 2026-05-11

### Added
- `commit-title-length-check` hook: enforce 50-character commit title limit (#186)

## [3.5.1] - 2026-05-11

### Added
- `external-write-falsify-check` hook: nested MCP body and positional `gh` body detection (#179)

## [3.5.0] - 2026-05-11

### Added
- `external-write-falsify-check` hook: advisory opt-in hook for hypothesis-stage text before external writes (#175)

## [3.4.0] - 2026-05-11

### Added
- `retrospect`: Gate-3 evidence robustness audit in Stage 2.5 (#172)

## [3.3.0] - 2026-05-09

### Added
- `retrospect`: explicit backing-repo gate before Stage 4 issue creation (#171)

## [3.2.0] - 2026-05-09

### Added
- `codex-review-wrap`: premise verification and flip detection across review rounds (#170)
- `codex-review-wrap`: fallback when codex-companion is unavailable (#166)

## [3.1.1] - 2026-05-08

### Fixed
- `codex-review-wrap`: use direct Node invocation instead of shell wrapper (#164)

## [3.1.0] - 2026-05-07

### Added
- `block-pr-without-caller-evidence` hook: gate `gh pr create` on caller-chain evidence in PR body (#159)

## [3.0.0] - 2026-05-06

### Added
- `codex-review-route` hook: warn on `/codex:review` in multi-worktree repos (#152)
- `memory-hint` hook: surface hookable memory entries by keyword at decision time (#150)

### Removed
- `debug` skill removed (#157)
- `turbo-complete`, `turbo-setup`, `turbo-deliver`, `cmux-orchestrator` skills removed (#155)

## [2.11.0] - 2026-04-30

### Added
- `retrospect`: memory-bias gate with 4-layer reinforcement (#147)

## [2.10.1] - 2026-04-29

### Changed
- `retrospect`: resolves backing repo from skill file location (#145)

## [2.10.0] - 2026-04-29

### Added
- `completion-verify` hook: require same-turn Bash verification evidence before completion claims (#144)

## [2.9.0] - 2026-04-28

### Added
- `codex-review-wrap` skill: worktree-aware wrapper for `/codex:review` with multi-worktree disambiguation (#141)

## [2.8.1] - 2026-04-27

### Added
- `cmux-browser` skill and CLI wrapper with SPA hydration wait protocol (#133)

### Fixed
- `strike`: scope state directory to praxis-owned path (#137)

## [2.8.0] - 2026-04-27

### Fixed
- `builtin-task-postuse` hook: correct false agent-spawn signal for built-in task tools (#135)

## [2.7.0] - 2026-04-26

### Added
- `block-gh-state-all` hook: hard-block invalid `gh search --state all` flag combination (#132)

## [2.6.1] - 2026-04-24

### Fixed
- Plugin packaging: drop `hooks` override to avoid duplicate auto-load (#125)

## [2.6.0] - 2026-04-24

### Added
- Multi-platform packaging with generated manifests; build and check scripts (#123)

## [2.5.0] - 2026-04-24

### Added
- `side-effect-scan` hook: pre-Bash scan for mutating commands (`git commit/push`, `gh pr merge/create`) (#122)

### Fixed
- `cmux-orchestrator`: harden codex result parsing (#121)

## [2.4.1] - 2026-04-24

### Added
- `turbo-setup`: next-step branching guide (#93)
- `strike`: gate 3/3 reset on reflection and persuasion (#105)

### Changed
- Routing: unify provider regex style across all skills (#120)

### Fixed
- `cmux-orchestrator`: replace `grep -oP` with macOS-compatible patterns (#112)

## [2.4.0] - 2026-04-21

### Added
- `strike` / `strikes` / `reset-strikes` skills: session-scoped three-strike discipline with Stop hook block (#103)

## [2.3.3] - 2026-04-16

### Added
- Auto-register `completion-verify` Stop hook via `plugin.json` (#101)

## [2.3.2] - 2026-04-16

### Added
- `turbo-setup`: auto-open cmux workspace after worktree creation (#95)
- `retrospect`: tool friction pass and upstream feedback action (#88)

### Fixed
- CLI: document codex exec write permissions (#94)

## [2.3.1] - 2026-04-14

### Added
- Multi-provider routing: route tasks to codex, gemini, or claude by keyword (#81)
- `cmux-delegate` v2: account, session, and distribute modes (#59)
- `cmux-delegate`: `--permission-mode` argument (#61)
- `recover`: show session UUID in list output (#74)
- `recover`: surface filter reasons in output (#75)
- `recover`: deduplicate conversation chains (#73)
- `retrospect`: surface multi-action improvement proposals (#86)
- CLI symlink install + verify script (#76)

### Fixed
- `recover`: prefer internal timestamp over mtime (#72)
- `recover`: robust `/exit` detection via user-only tail (#71)
- `retrospect`: deduplicate memory entries before creating (#80)

## [2.3.0] - 2026-04-09

### Added
- `retrospect`: escalation logic and mandatory agent calls (#50)

### Changed
- Consolidated workflow into `turbo-completion` skill (#55)

### Removed
- `brainstorm` skill removed (#53)

## [2.2.0] - 2026-04-09

### Added
- `cmux-delegate` skill: delegate tasks to independent cmux sessions (#48)

## [2.1.0] - 2026-04-08

### Added
- `turbo-implement` skill (#44)

### Changed
- All skills made project-agnostic (#46)
- Merged `finish-branch` into `turbo-deliver`

## [2.0.0] - 2026-04-08

### Changed
- Project renamed from `my-skills` to `praxis`; all references updated (#40)

## [1.4.0] - 2026-04-08

### Added
- `cmux-save-sessions` and `cmux-resume-sessions` skills (#39)

## [1.3.0] - 2026-03-31

### Added
- `retrospect` skill: session retrospect with friction analysis (#37)

### Fixed
- `cmux-recover-sessions`: workspace creation and plain mode (#32)

## [1.2.0] - 2026-03-27

### Added
- `cmux-session-manager` skill: daily session lifecycle management (#28)

### Changed
- `recover-sessions-cmux` renamed to `cmux-recover-sessions` (#30)

## [1.1.0] - 2026-03-26

### Added
- `recover-sessions` skill: bulk session recovery after power loss (#18)
- `cmux-recover-sessions` skill: cmux-backed session recovery (#20)
- Unified workflow skills: turbo-setup, turbo-deliver, cmux-orchestrator (#13, #24)
- `pr-dev-to-prod` skill: release PR from dev to prod (#3)
- Plugin-based architecture for install-claude-stack (#7)

### Changed
- Shared scan module extracted from skills (#26)

### Fixed
- `finish-branch`: reorder compounding before merge (#16)

[3.17.0]: https://github.com/devseunggwan/praxis/compare/v3.16.0...v3.17.0
[3.16.0]: https://github.com/devseunggwan/praxis/compare/v3.15.0...v3.16.0
[3.15.0]: https://github.com/devseunggwan/praxis/compare/v3.14.0...v3.15.0
[3.14.0]: https://github.com/devseunggwan/praxis/compare/v3.13.0...v3.14.0
[3.13.0]: https://github.com/devseunggwan/praxis/compare/v3.12.0...v3.13.0
[3.12.0]: https://github.com/devseunggwan/praxis/compare/v3.11.0...v3.12.0
[3.11.0]: https://github.com/devseunggwan/praxis/compare/v3.10.0...v3.11.0
[3.10.0]: https://github.com/devseunggwan/praxis/compare/v3.9.0...v3.10.0
[3.9.0]: https://github.com/devseunggwan/praxis/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/devseunggwan/praxis/compare/v3.7.0...v3.8.0
[3.7.0]: https://github.com/devseunggwan/praxis/compare/v3.6.0...v3.7.0
[3.6.0]: https://github.com/devseunggwan/praxis/compare/v3.5.1...v3.6.0
[3.5.1]: https://github.com/devseunggwan/praxis/compare/v3.5.0...v3.5.1
[3.5.0]: https://github.com/devseunggwan/praxis/compare/v3.4.0...v3.5.0
[3.4.0]: https://github.com/devseunggwan/praxis/compare/v3.3.0...v3.4.0
[3.3.0]: https://github.com/devseunggwan/praxis/compare/v3.2.0...v3.3.0
[3.2.0]: https://github.com/devseunggwan/praxis/compare/v3.1.1...v3.2.0
[3.1.1]: https://github.com/devseunggwan/praxis/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/devseunggwan/praxis/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/devseunggwan/praxis/compare/v2.11.0...v3.0.0
[2.11.0]: https://github.com/devseunggwan/praxis/compare/v2.10.1...v2.11.0
[2.10.1]: https://github.com/devseunggwan/praxis/compare/v2.10.0...v2.10.1
[2.10.0]: https://github.com/devseunggwan/praxis/compare/v2.9.0...v2.10.0
[2.9.0]: https://github.com/devseunggwan/praxis/compare/v2.8.1...v2.9.0
[2.8.1]: https://github.com/devseunggwan/praxis/compare/v2.8.0...v2.8.1
[2.8.0]: https://github.com/devseunggwan/praxis/compare/v2.7.0...v2.8.0
[2.7.0]: https://github.com/devseunggwan/praxis/compare/v2.6.1...v2.7.0
[2.6.1]: https://github.com/devseunggwan/praxis/compare/v2.6.0...v2.6.1
[2.6.0]: https://github.com/devseunggwan/praxis/compare/v2.5.0...v2.6.0
[2.5.0]: https://github.com/devseunggwan/praxis/compare/v2.4.1...v2.5.0
[2.4.1]: https://github.com/devseunggwan/praxis/compare/v2.4.0...v2.4.1
[2.4.0]: https://github.com/devseunggwan/praxis/compare/v2.3.3...v2.4.0
[2.3.3]: https://github.com/devseunggwan/praxis/compare/v2.3.2...v2.3.3
[2.3.2]: https://github.com/devseunggwan/praxis/compare/v2.3.1...v2.3.2
[2.3.1]: https://github.com/devseunggwan/praxis/compare/v2.3.0...v2.3.1
[2.3.0]: https://github.com/devseunggwan/praxis/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/devseunggwan/praxis/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/devseunggwan/praxis/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/devseunggwan/praxis/compare/v1.4.0...v2.0.0
[1.4.0]: https://github.com/devseunggwan/praxis/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/devseunggwan/praxis/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/devseunggwan/praxis/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/devseunggwan/praxis/releases/tag/v1.1.0
