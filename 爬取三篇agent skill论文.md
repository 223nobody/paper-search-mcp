# 爬取三篇 agent skill 论文

> 请你使用 mcp 工具为我爬取三篇 agent skill 相关论文

<details><summary>8 previous messages</summary>

> 我先找一下这个环境里暴露的论文检索 MCP 工具，然后用它按“agent skill / skill learning / tool-using agents”这一类关键词抓取三篇相关论文的信息。
>
> 我已经找到 `paper_search_mcp` 的检索和下载接口了。接下来我会先检索候选论文，再挑三篇和“Agent 的技能学习/技能库/工具技能”最贴近的，最后尽量把 PDF 也拉到本地。
>
> MCP tool call
>
> paper-search-mcp.search_papers
>
> ```json
> {
>   "query": "LLM agent skill learning skill library autonomous agents",
>   "sources": "arxiv,semantic,openalex,crossref",
>   "max_results_per_source": 5
> }
> ```
>
> ```text
> {
>   "query": "LLM agent skill learning skill library autonomous agents",
>   "sources_requested": "arxiv,semantic,openalex,crossref",
>   "sources_used": [
>     "arxiv",
>     "semantic",
>     "openalex",
>     "crossref"
>   ],
>   "source_results": {
>     "arxiv": 5,
>     "semantic": 0,
>     "openalex": 5,
>     "crossref": 5
>   },
>   "errors": {},
>   "papers": [
>     {
>       "paper_id": "2605.27955v1",
>       "title": "Skill-as-Pseudocode: Refactoring Skill Libraries to Pseudocode for LLM Agents",
>       "authors": "Xinze Li; Yuhang Zang; Yixin Cao; Aixin Sun",
>       "abstract": "Markdown skill libraries for LLM agents ship as free-form prose, forcing the agent to re-derive both the input schema and the concrete invocation syntax on every retrieval. We observe that this often produces a \"confused -> re-retrieve -> still confused\" loop in which the agent issues a partially-correct action, receives uninformative environment feedback, and re-retrieves the same prose. We propose Skill-as-Pseudocode (SaP), an automatic conversion of markdown skill libraries into typed pseudocode with deterministic quality control. For each cluster of similar procedural passages drawn from one or more skills, SaP extracts a typed contract and filters it through a four-check deterministic verifier (coverage, binding, replacement, risk). Promoted contracts are inlined into a rewritten skill skeleton together with restored concrete action templates, giving the agent two complementary signals: a typed signature for what the skill does and a concrete template for how to invoke it. On the 134-game ALFWorld unseen split with gpt-4o-mini, pooled across three seeds, SaP wins 82/402 paired games versus 47/402 for the Graph-of-Skills (GoS) baseline (pooled McNemar p = 8.2e-5), at -22.8 +/- 6.4% input tokens and -14.5 +/- 4.1% LLM calls per game.",
>       "doi": "",
>       "published_date": "2026-05-27T04:48:40",
>       "pdf_url": "https://arxiv.org/pdf/2605.27955v1",
>       "url": "http://arxiv.org/abs/2605.27955v1",
>       "source": "arxiv",
>       "updated_date": "2026-05-27T04:48:40",
>       "categories": "cs.PL; cs.CL",
>       "keywords": "",
>       "citations": 0,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "2405.15019v2",
>       "title": "Agentic Skill Discovery",
>       "authors": "Xufeng Zhao; Cornelius Weber; Stefan Wermter",
>       "abstract": "Language-conditioned robotic skills make it possible to apply the high-level reasoning of Large Language Models (LLMs) to low-level robotic control. A remaining challenge is to acquire a diverse set of fundamental skills. Existing approaches either manually decompose a complex task into atomic robotic actions in a top-down fashion, or bootstrap as many combinations as possible in a bottom-up fashion to cover a wider range of task possibilities. These decompositions or combinations, however, require an initial skill library. For example, a ``grasping'' capability can never emerge from a skill library containing only diverse ``pushing'' skills. Existing skill discovery techniques with reinforcement learning acquire skills by an exhaustive exploration but often yield non-meaningful behaviors. In this study, we introduce a novel framework for skill discovery that is entirely driven by LLMs. The framework begins with an LLM generating task proposals based on the provided scene description and the robot's configurations, aiming to incrementally acquire new skills upon task completion. For each proposed task, a series of reinforcement learning processes are initiated, utilizing reward and success determination functions sampled by the LLM to develop the corresponding policy. The reliability and trustworthiness of learned behaviors are further ensured by an independent vision-language model. We show that starting with zero skill, the skill library emerges and expands to more and more meaningful and reliable skills, enabling the robot to efficiently further propose and complete advanced tasks. Project page: \\url{https://agentic-skill-discovery.github.io}.",
>       "doi": "",
>       "published_date": "2024-05-23T19:44:03",
>       "pdf_url": "https://arxiv.org/pdf/2405.15019v2",
>       "url": "http://arxiv.org/abs/2405.15019v2",
>       "source": "arxiv",
>       "updated_date": "2024-08-16T15:56:46",
>       "categories": "cs.RO; cs.AI; cs.LG",
>       "keywords": "",
>       "citations": 0,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "2605.19362v2",
>       "title": "Toward User Comprehension Supports for LLM Agent Skill Specifications",
>       "authors": "Zikai Alex Wen",
>       "abstract": "Users often interpret and select agent skills through their SKILL markdown specifications. To protect users, existing audits mainly focus on malicious or unsafe skills. We study the complementary question of whether specifications help users form bounded expectations about what a skill consumes, produces, and covers. Across 878 cybersecurity skills, we used rule-based coding to measure textual cues for four comprehension anchors, namely operational basis, output contract, boundary disclosure, and example capability demonstration. Cues for operational basis were common, but only 19.0% of specifications exhibited cues for an example task, sample, or expected outcome, and only 2.3% exhibited cues for all four anchors. We further examined a small DNS/C2 telemetry subset (n$=$6) to illustrate why missing examples may matter. Examples appeared to make first local checks easier to construct, while no-example skills typically required helper code inspection to recover command arguments or output fields. We argue that agent-skill evaluation should treat specifications as user-facing capability disclosures, not merely as containers for executable instructions.",
>       "doi": "",
>       "published_date": "2026-05-19T04:50:42",
>       "pdf_url": "https://arxiv.org/pdf/2605.19362v2",
>       "url": "http://arxiv.org/abs/2605.19362v2",
>       "source": "arxiv",
>       "updated_date": "2026-05-20T17:49:17",
>       "categories": "cs.HC; cs.AI",
>       "keywords": "",
>       "citations": 0,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "2605.03353v4",
>       "title": "SkCC: Portable and Secure Skill Compilation for Cross-Framework LLM Agents",
>       "authors": "Yipeng Ouyang; Yi Xiao; Yuhao Gu; Xianwei Zhang",
>       "abstract": "LLM agents increasingly rely on reusable skills (e.g., SKILL markdown files) to execute complex tasks, yet these artifacts lack portability: agent frameworks are highly sensitive to prompt formatting, leading to a large performance variation for the same skill. Nevertheless, most skills are authored once as format-agnostic Markdown, necessitating costly per-framework rewrites and also leaving security largely unaddressed, with widespread vulnerabilities in practice. To address this, we present SkCC, a compiler for LLM agents that introduces classical compilation design into agent skill development. SkCC centers on SkIR, a strongly-typed intermediate representation that decouples skill semantics from framework-specific formatting, thus enabling portable deployment across agent frameworks. Atop of this IR, a static Optimizer enforces security constraints, blocking vulnerabilities before deployment. Implemented as a four-phase pipeline, SkCC effectively reduces adaptation complexity from $O(m \\times n)$ to $O(m + n)$ across $m$ skills and $n$ frameworks. Experiments on SkillsBench demonstrate that SkCC delivers consistent and substantial gains over original counterparts, with pass rate increases from 21.1% to 33.3% on Claude Code and from 35.1% to 48.7% on Kimi CLI. Further, the design achieves sub-10ms compilation latency, 94.8% proactive security trigger rate, and 10-46% runtime token savings across frameworks.",
>       "doi": "",
>       "published_date": "2026-05-05T04:15:48",
>       "pdf_url": "https://arxiv.org/pdf/2605.03353v4",
>       "url": "http://arxiv.org/abs/2605.03353v4",
>       "source": "arxiv",
>       "updated_date": "2026-06-03T02:50:21",
>       "categories": "cs.CR; cs.AI",
>       "keywords": "",
>       "citations": 0,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "2604.10674v1",
>       "title": "Skill-SD: Skill-Conditioned Self-Distillation for Multi-turn LLM Agents",
>       "authors": "Hao Wang; Guozhi Wang; Han Xiao; Yufeng Zhou; Yue Pan; Jichao Wang; Ke Xu; Yafei Wen; Xiaohu Ruan; Xiaoxin Chen; Honggang Qi",
>       "abstract": "Reinforcement learning (RL) has been widely used to train LLM agents for multi-turn interactive tasks, but its sample efficiency is severely limited by sparse rewards and long horizons. On-policy self-distillation (OPSD) alleviates this by providing dense token-level supervision from a privileged teacher that has access to ground-truth answers. However, such fixed privileged information cannot capture the diverse valid strategies in agent tasks, and naively combining OPSD with RL often leads to training collapse. To address these limitations, we introduce Skill-SD, a framework that turns the agent's own trajectories into dynamic training-only supervision. Completed trajectories are summarized into compact natural language skills that describe successful behaviors, mistakes, and workflows. These skills serve as dynamic privileged information conditioning only the teacher, while the student always acts under the plain task prompt and learns to internalize the guidance through distillation. To stabilize the training, we derive an importance-weighted reverse-KL loss to provide gradient-correct token-level distillation, and dynamically synchronize the teacher with the improving student. Experimental results on agentic benchmarks demonstrate that Skill-SD substantially outperforms the standard RL baseline, improving both vanilla GRPO (+14.0%/+10.9% on AppWorld/Sokoban) and vanilla OPD (+42.1%/+40.6%). Project page: https://k1xe.github.io/skill-sd/",
>       "doi": "",
>       "published_date": "2026-04-12T14:57:52",
>       "pdf_url": "https://arxiv.org/pdf/2604.10674v1",
>       "url": "http://arxiv.org/abs/2604.10674v1",
>       "source": "arxiv",
>       "updated_date": "2026-04-12T14:57:52",
>       "categories": "cs.LG; cs.AI; cs.CL",
>       "keywords": "",
>       "citations": 0,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "W4393065402",
>       "title": "A survey on large language model based autonomous agents",
>       "authors": "Lei Wang; Chen Ma; Xueyang Feng; Zeyu Zhang; Hao Yang; Jingsen Zhang; Zhiyuan Chen; Jiakai Tang; Xu Chen; Yankai Lin; Wayne Xin Zhao; Zhewei Wei; Ji-Rong Wen",
>       "abstract": "Abstract Autonomous agents have long been a research focus in academic and industry communities. Previous research often focuses on training agents with limited knowledge within isolated environments, which diverges significantly from human learning processes, and makes the agents hard to achieve human-like decisions. Recently, through the acquisition of vast amounts of Web knowledge, large language models (LLMs) have shown potential in human-level intelligence, leading to a surge in research on LLM-based autonomous agents. In this paper, we present a comprehensive survey of these studies, delivering a systematic review of LLM-based autonomous agents from a holistic perspective. We first discuss the construction of LLM-based autonomous agents, proposing a unified framework that encompasses much of previous work. Then, we present a overview of the diverse applications of LLM-based autonomous agents in social science, natural science, and engineering. Finally, we delve into the evaluation strategies commonly used for LLM-based autonomous agents. Based on the previous studies, we also present several challenges and future directions in this field.",
>       "doi": "10.1007/s11704-024-40231-1",
>       "published_date": "2024-03-22T00:00:00",
>       "pdf_url": "https://link.springer.com/content/pdf/10.1007/s11704-024-40231-1.pdf",
>       "url": "https://doi.org/10.1007/s11704-024-40231-1",
>       "source": "openalex",
>       "updated_date": "",
>       "categories": "Computer science; Artificial intelligence",
>       "keywords": "",
>       "citations": 1108,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "W4405185373",
>       "title": "A review of large language models and autonomous agents in chemistry",
>       "authors": "Mayk Caldas Ramos; Christopher J. Collison; Andrew Dickson White",
>       "abstract": "Large language models (LLMs) have emerged as powerful tools in chemistry, significantly impacting molecule design, property prediction, and synthesis optimization. This review highlights LLM capabilities in these domains and their potential to accelerate scientific discovery through automation. We also review LLM-based autonomous agents: LLMs with a broader set of tools to interact with their surrounding environment. These agents perform diverse tasks such as paper scraping, interfacing with automated laboratories, and synthesis planning. As agents are an emerging topic, we extend the scope of our review of agents beyond chemistry and discuss across any scientific domains. This review covers the recent history, current capabilities, and design of LLMs and autonomous agents, addressing specific challenges, opportunities, and future directions in chemistry. Key challenges include data quality and integration, model interpretability, and the need for standard benchmarks, while future directions point towards more sophisticated multi-modal agents and enhanced collaboration between agents and experimental methods. Due to the quick pace of this field, a repository has been built to keep track of the latest studies: https://github.com/ur-whitelab/LLMs-in-science.",
>       "doi": "10.1039/d4sc03921a",
>       "published_date": "2024-12-09T00:00:00",
>       "pdf_url": "https://doi.org/10.1039/d4sc03921a",
>       "url": "https://doi.org/10.1039/d4sc03921a",
>       "source": "openalex",
>       "updated_date": "",
>       "categories": "Chemistry; Computer science; Biochemical engineering; Cognitive science; Psychology",
>       "keywords": "",
>       "citations": 186,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "W4362515116",
>       "title": "A Survey of Large Language Models",
>       "authors": "Wayne Xin Zhao; Kun Zhou; Junyi Li; Tianyi Tang; Xiaolei Wang; Yupeng Hou; Yingqian Min; Beichen Zhang; Junjie Zhang; Zican Dong; Yifan Du; Yang Chen; Yushuo Chen; Zhipeng Chen; Jinhao Jiang; Ruiyang Ren; Yifan Li; Xinyu Tang; Zikang Liu; Peiyu Liu; Jian‐Yun Nie; Ji-Rong Wen; Ji-Rong Wen",
>       "abstract": "Abstract The rapid evolution of large language models (LLMs) has driven a transformative shift in artificial intelligence (AI), reshaping both research paradigms and practical applications. Distinguished from their predecessors by unprecedented scale and advanced capabilities, LLMs necessitate new frameworks for understanding their development, behavior, and societal impact. This survey systematically reviews recent advancements in LLM techniques across four key dimensions: (1) pre-training methodologies, which establish core model capabilities through large-scale self-supervised training, architectural innovations, and data curation strategies; (2) post-training techniques, including supervised fine-tuning and reinforcement learning, which adapt foundational models to downstream tasks and enhance their alignment and safety; (3) utilization strategies, such as in-context learning, prompt engineering, and agentic reasoning, that optimize real-world deployment and enable effective interaction with external environments; and (4) evaluation methods, encompassing benchmarks for key ability dimensions such as core language capabilities, reasoning, and safety, which support comprehensive and reliable assessment of model performance. Additionally, we identify critical research issues, including those concerning theoretical foundations, efficient scaling, alignment, and agentic capability, and highlight the open challenges they present. By synthesizing state-of-the-art insights and emerging trends, this survey aims to provide a systematic and comprehensive framework for understanding the trajectory, current limitations, and future directions of LLM progress.",
>       "doi": "10.1007/s11704-026-60308-3",
>       "published_date": "2026-05-09T00:00:00",
>       "pdf_url": "https://link.springer.com/content/pdf/10.1007/s11704-026-60308-3.pdf",
>       "url": "https://doi.org/10.1007/s11704-026-60308-3",
>       "source": "openalex",
>       "updated_date": "",
>       "categories": "Language model; Computer science; Mainstream; Scale (ratio); Artificial intelligence",
>       "keywords": "",
>       "citations": 1401,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "W4386184788",
>       "title": "A Survey on Large Language Model based Autonomous Agents",
>       "authors": "Lei Wang; Chen Ma; Xueyang Feng; Zeyu Zhang; Hao Yang; Jingsen Zhang; Zhiyuan Chen; Jiakai Tang; Xu Chen; Yankai Lin; Wayne Xin Zhao; Zhewei Wei; Ji-Rong Wen",
>       "abstract": "Autonomous agents have long been a prominent research focus in both academic and industry communities. Previous research in this field often focuses on training agents with limited knowledge within isolated environments, which diverges significantly from human learning processes, and thus makes the agents hard to achieve human-like decisions. Recently, through the acquisition of vast amounts of web knowledge, large language models (LLMs) have demonstrated remarkable potential in achieving human-level intelligence. This has sparked an upsurge in studies investigating LLM-based autonomous agents. In this paper, we present a comprehensive survey of these studies, delivering a systematic review of the field of LLM-based autonomous agents from a holistic perspective. More specifically, we first discuss the construction of LLM-based autonomous agents, for which we propose a unified framework that encompasses a majority of the previous work. Then, we present a comprehensive overview of the diverse applications of LLM-based autonomous agents in the fields of social science, natural science, and engineering. Finally, we delve into the evaluation strategies commonly used for LLM-based autonomous agents. Based on the previous studies, we also present several challenges and future directions in this field. To keep track of this field and continuously update our survey, we maintain a repository of relevant references at https://github.com/Paitesanshi/LLM-Agent-Survey.",
>       "doi": "10.48550/arxiv.2308.11432",
>       "published_date": "2023-08-22T00:00:00",
>       "pdf_url": "https://arxiv.org/pdf/2308.11432",
>       "url": "http://arxiv.org/abs/2308.11432",
>       "source": "openalex",
>       "updated_date": "",
>       "categories": "Field (mathematics); Computer science; Data science; Autonomous agent; Perspective (graphical)",
>       "keywords": "",
>       "citations": 113,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "W4382656966",
>       "title": "GPT-3-Driven Pedagogical Agents to Train Children’s Curious Question-Asking Skills",
>       "authors": "Rania Abdelghani; Yen-Hsiang Wang; Xingdi Yuan; Tong Wang; Pauline Lucas; Hélène Sauzeon; Pierre-Yves Oudeyer",
>       "abstract": "",
>       "doi": "10.1007/s40593-023-00340-7",
>       "published_date": "2023-06-30T00:00:00",
>       "pdf_url": "https://hal.science/hal-03890447/document",
>       "url": "https://doi.org/10.1007/s40593-023-00340-7",
>       "source": "openalex",
>       "updated_date": "",
>       "categories": "Mathematics education; Educational technology; Computer science; Multimedia; Psychology",
>       "keywords": "",
>       "citations": 109,
>       "references": "",
>       "extra": ""
>     },
>     {
>       "paper_id": "10.65109/fmju3241",
>       "title": "Hierarchical Cooperative Multi-Agent Reinforcement Learning with Skill Discovery",
>       "authors": "Jiachen Yang; Igor Borovikov; Hongyuan Zha",
>       "abstract": "<jats:p>Human players in professional team sports achieve high level coordination by dynamically choosing complementary skills and executing primitive actions to perform these skills. As a step toward creating intelligent agents with this capability for fully cooperative multi-agent settings, we propose a two-level hierarchical multi-agent reinforcement learning (MARL) algorithm with unsupervised skill discovery. Agents learn useful and distinct skills at the low level via independent Q-learning, while they learn to select complementary latent skill variables at the high level via centralized multi-agent training with an extrinsic team reward. The set of low-level skills emerges from an intrinsic reward that solely promotes the decodability of latent skill variables from the trajectory of a low-level skill, without the need for hand-crafted rewards for each skill. For scalable decentralized execution, each agent independently chooses latent skill variables and primitive actions based on local observations. Our overall method enables the use of general cooperative MARL algorithms for training high level policies and single-agent RL for training low level skills. Experiments on a stochastic high dimensional team game show the emergence of useful skills and cooperative team play. The interpretability of the learned skills show the promise of the proposed method for achieving human-AI cooperation in team sports games.</jats:p>",
>       "doi": "10.65109/fmju3241",
>       "published_date": "2020-05-05T00:00:00",
>       "pdf_url": "",
>       "url": "https://doi.org/10.65109/fmju3241",
>       "source": "crossref",
>       "updated_date": "",
>       "categories": "proceedings-article",
>       "keywords": "",
>       "citations": 4,
>       "references": "",
>       "extra": "{'publisher': 'IEEE Computer Society', 'container_title': 'International Joint Conference on Autonomous Agents and Multiagent Systems', 'volume': '', 'issue': '', 'page': '1566-1574', 'issn': ['1558-2914'], 'isbn': [], 'crossref_type': 'proceedings-article', 'member': '54598', 'prefix': '10.65109'}"
>     },
>     {
>       "paper_id": "10.65109/brag3288",
>       "title": "IntentCUA: Learning Intent-level Representations for Skill Abstraction and Multi-Agent Planning in Computer-Use Agents",
>       "authors": "Seoyoung Lee; Seobin Yoon; Seongbeen Lee; Yoojung Chun; Dayoung Park; Doyeon Kim; Joo Yong Sim",
>       "abstract": "",
>       "doi": "10.65109/brag3288",
>       "published_date": "1970-01-01T00:00:00",
>       "pdf_url": "",
>       "url": "https://doi.org/10.65109/brag3288",
>       "source": "crossref",
>       "updated_date": "",
>       "categories": "proceedings-article",
>       "keywords": "",
>       "citations": 0,
>       "references": "",
>       "extra": "{'publisher': 'International Foundation for Autonomous Agents and Multiagent Systems', 'container_title': 'Proceedings of the 25th International Conference on Autonomous Agents and Multiagent Systems', 'volume': '', 'issue': '', 'page': '', 'issn': [], 'isbn': [], 'crossref_type': 'proceedings-article', 'member': '54598', 'prefix': '10.65109'}"
>     },
>     {
>       "paper_id": "10.2139/ssrn.6438922",
>       "title": "Beyond Prescribed Intelligence: Toward Self-Directed Skill Acquisition in LLM-Based Cybersecurity Agents",
>       "authors": "Mika D Ayenson",
>       "abstract": "<jats:p>The dominant paradigm for integrating Large Language Models (LLMs) into Security Operations Centers (SOCs), which this paper terms prescribed intelligence (human-engineered skills, hardcoded tool integrations, and prompt templates), is structurally insufficient for replicating the adaptive workflows of expert SOC analysts. This paper identifies five limitations of that paradigm and proposes credentialed self-exploration as an alternative: provision an LLM agent with strictly read-only access to a sandboxed or test SOC environment, describe its role, and let it autonomously discover data sources, learn tool relationships, and propose executable skills for human review. During exploration, the agent takes no action on production systems; only skills that pass human review are deployed. The paper introduces AESOP (Agent-driven Exploration for Security Operations Proficiency), a conceptual framework for this self-directed architecture, and outlines a research agenda for empirical validation. Supporting evidence draws on cross-domain advances in tool learning, autonomous agents, and offensive security (penetration testing), demonstrating patterns transferable to defensive SOC operations-a central question this work raises.</jats:p>",
>       "doi": "10.2139/ssrn.6438922",
>       "published_date": "2026-01-01T00:00:00",
>       "pdf_url": "",
>       "url": "https://doi.org/10.2139/ssrn.6438922",
>       "source": "crossref",
>       "updated_date": "",
>       "categories": "posted-content",
>       "keywords": "",
>       "citations": 0,
>       "references": "",
>       "extra": "{'publisher': 'Elsevier BV', 'container_title': '', 'volume': '', 'issue': '', 'page': '', 'issn': [], 'isbn': [], 'crossref_type': 'posted-content', 'member': '78', 'prefix': '10.2139'}"
>     },
>     {
>       "paper_id": "10.65109/xuim2827",
>       "title": "Autonomous Skill Acquisition for Robots Using Graduated Learning",
>       "authors": "Gautham Vasan",
>       "abstract": "<jats:p>Skill acquisition is among the most remarkable aspects of human intelligence. It involves discovering purposeful behavioural modules, retaining them as skills, honing them through practice, and applying them in unforeseen circumstances [11]. Skill acquisition underlies our ability to choose to spend time and energy on the mastery of particular tasks and draw upon previous experience to solve more complex problems over time with less cognitive effort[10]. If endowed with continual skill acquisition, robots can autonomously improve their skills over time, where learning at one stage of development is a foundation for future learning [23]. It could unlock new possibilities for physical automation with general-purpose robots, just as general-purpose computer processors ushered in the information age [24, 33]. In this work, we propose a novel approach called Graduated Learning, where we ask a robot to acquire new manipulation and locomotion skills repeatedly, using time-delineated experiences of attempts at those skills (i.e., episodes) and some store of previously acquired knowledge (e.g., weights of a neural network). Our proposed approach chooses the order in which an agent learns these skills since the progressive manner in which they are developed plays a vital role in developing a final skill set.</jats:p>",
>       "doi": "10.65109/xuim2827",
>       "published_date": "2024-05-06T00:00:00",
>       "pdf_url": "",
>       "url": "https://doi.org/10.65109/xuim2827",
>       "source": "crossref",
>       "updated_date": "",
>       "categories": "proceedings-article",
>       "keywords": "",
>       "citations": 0,
>       "references": "",
>       "extra": "{'publisher': 'IEEE Computer Society', 'container_title': 'International Joint Conference on Autonomous Agents and Multiagent Systems', 'volume': '', 'issue': '', 'page': '2785-2787', 'issn': ['1558-2914'], 'isbn': [], 'crossref_type': 'proceedings-article', 'member': '54598', 'prefix': '10.65109'}"
>     },
>     {
>       "paper_id": "10.2139/ssrn.4790564",
>       "title": "Skill Matters: Dynamic Skill Learning for Multi-Agent Cooperative Reinforcement Learning",
>       "authors": "Tong Li; Chenjia Bai; Kang Xu; Chen Chu; Peican Zhu; Zhen Wang",
>       "abstract": "",
>       "doi": "10.2139/ssrn.4790564",
>       "published_date": "2024-01-01T00:00:00",
>       "pdf_url": "",
>       "url": "https://doi.org/10.2139/ssrn.4790564",
>       "source": "crossref",
>       "updated_date": "",
>       "categories": "posted-content",
>       "keywords": "",
>       "citations": 0,
>       "references": "",
>       "extra": "{'publisher': 'Elsevier BV', 'container_title': '', 'volume': '', 'issue': '', 'page': '', 'issn': [], 'isbn': [], 'crossref_type': 'posted-content', 'member': '78', 'prefix': '10.2139'}"
>     }
>   ],
>   "total": 15,
>   "raw_total": 15
> }
> ```
>
> ```json
> {
>   "result": {
>     "query": "LLM agent skill learning skill library autonomous agents",
>     "sources_requested": "arxiv,semantic,openalex,crossref",
>     "sources_used": ["arxiv", "semantic", "openalex", "crossref"],
>     "source_results": {
>       "arxiv": 5,
>       "semantic": 0,
>       "openalex": 5,
>       "crossref": 5
>     },
>     "errors": {},
>     "papers": [
>       {
>         "paper_id": "2605.27955v1",
>         "title": "Skill-as-Pseudocode: Refactoring Skill Libraries to Pseudocode for LLM Agents",
>         "authors": "Xinze Li; Yuhang Zang; Yixin Cao; Aixin Sun",
>         "abstract": "Markdown skill libraries for LLM agents ship as free-form prose, forcing the agent to re-derive both the input schema and the concrete invocation syntax on every retrieval. We observe that this often produces a \"confused -> re-retrieve -> still confused\" loop in which the agent issues a partially-correct action, receives uninformative environment feedback, and re-retrieves the same prose. We propose Skill-as-Pseudocode (SaP), an automatic conversion of markdown skill libraries into typed pseudocode with deterministic quality control. For each cluster of similar procedural passages drawn from one or more skills, SaP extracts a typed contract and filters it through a four-check deterministic verifier (coverage, binding, replacement, risk). Promoted contracts are inlined into a rewritten skill skeleton together with restored concrete action templates, giving the agent two complementary signals: a typed signature for what the skill does and a concrete template for how to invoke it. On the 134-game ALFWorld unseen split with gpt-4o-mini, pooled across three seeds, SaP wins 82/402 paired games versus 47/402 for the Graph-of-Skills (GoS) baseline (pooled McNemar p = 8.2e-5), at -22.8 +/- 6.4% input tokens and -14.5 +/- 4.1% LLM calls per game.",
>         "doi": "",
>         "published_date": "2026-05-27T04:48:40",
>         "pdf_url": "https://arxiv.org/pdf/2605.27955v1",
>         "url": "http://arxiv.org/abs/2605.27955v1",
>         "source": "arxiv",
>         "updated_date": "2026-05-27T04:48:40",
>         "categories": "cs.PL; cs.CL",
>         "keywords": "",
>         "citations": 0,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "2405.15019v2",
>         "title": "Agentic Skill Discovery",
>         "authors": "Xufeng Zhao; Cornelius Weber; Stefan Wermter",
>         "abstract": "Language-conditioned robotic skills make it possible to apply the high-level reasoning of Large Language Models (LLMs) to low-level robotic control. A remaining challenge is to acquire a diverse set of fundamental skills. Existing approaches either manually decompose a complex task into atomic robotic actions in a top-down fashion, or bootstrap as many combinations as possible in a bottom-up fashion to cover a wider range of task possibilities. These decompositions or combinations, however, require an initial skill library. For example, a ``grasping'' capability can never emerge from a skill library containing only diverse ``pushing'' skills. Existing skill discovery techniques with reinforcement learning acquire skills by an exhaustive exploration but often yield non-meaningful behaviors. In this study, we introduce a novel framework for skill discovery that is entirely driven by LLMs. The framework begins with an LLM generating task proposals based on the provided scene description and the robot's configurations, aiming to incrementally acquire new skills upon task completion. For each proposed task, a series of reinforcement learning processes are initiated, utilizing reward and success determination functions sampled by the LLM to develop the corresponding policy. The reliability and trustworthiness of learned behaviors are further ensured by an independent vision-language model. We show that starting with zero skill, the skill library emerges and expands to more and more meaningful and reliable skills, enabling the robot to efficiently further propose and complete advanced tasks. Project page: \\url{https://agentic-skill-discovery.github.io}.",
>         "doi": "",
>         "published_date": "2024-05-23T19:44:03",
>         "pdf_url": "https://arxiv.org/pdf/2405.15019v2",
>         "url": "http://arxiv.org/abs/2405.15019v2",
>         "source": "arxiv",
>         "updated_date": "2024-08-16T15:56:46",
>         "categories": "cs.RO; cs.AI; cs.LG",
>         "keywords": "",
>         "citations": 0,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "2605.19362v2",
>         "title": "Toward User Comprehension Supports for LLM Agent Skill Specifications",
>         "authors": "Zikai Alex Wen",
>         "abstract": "Users often interpret and select agent skills through their SKILL markdown specifications. To protect users, existing audits mainly focus on malicious or unsafe skills. We study the complementary question of whether specifications help users form bounded expectations about what a skill consumes, produces, and covers. Across 878 cybersecurity skills, we used rule-based coding to measure textual cues for four comprehension anchors, namely operational basis, output contract, boundary disclosure, and example capability demonstration. Cues for operational basis were common, but only 19.0% of specifications exhibited cues for an example task, sample, or expected outcome, and only 2.3% exhibited cues for all four anchors. We further examined a small DNS/C2 telemetry subset (n$=$6) to illustrate why missing examples may matter. Examples appeared to make first local checks easier to construct, while no-example skills typically required helper code inspection to recover command arguments or output fields. We argue that agent-skill evaluation should treat specifications as user-facing capability disclosures, not merely as containers for executable instructions.",
>         "doi": "",
>         "published_date": "2026-05-19T04:50:42",
>         "pdf_url": "https://arxiv.org/pdf/2605.19362v2",
>         "url": "http://arxiv.org/abs/2605.19362v2",
>         "source": "arxiv",
>         "updated_date": "2026-05-20T17:49:17",
>         "categories": "cs.HC; cs.AI",
>         "keywords": "",
>         "citations": 0,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "2605.03353v4",
>         "title": "SkCC: Portable and Secure Skill Compilation for Cross-Framework LLM Agents",
>         "authors": "Yipeng Ouyang; Yi Xiao; Yuhao Gu; Xianwei Zhang",
>         "abstract": "LLM agents increasingly rely on reusable skills (e.g., SKILL markdown files) to execute complex tasks, yet these artifacts lack portability: agent frameworks are highly sensitive to prompt formatting, leading to a large performance variation for the same skill. Nevertheless, most skills are authored once as format-agnostic Markdown, necessitating costly per-framework rewrites and also leaving security largely unaddressed, with widespread vulnerabilities in practice. To address this, we present SkCC, a compiler for LLM agents that introduces classical compilation design into agent skill development. SkCC centers on SkIR, a strongly-typed intermediate representation that decouples skill semantics from framework-specific formatting, thus enabling portable deployment across agent frameworks. Atop of this IR, a static Optimizer enforces security constraints, blocking vulnerabilities before deployment. Implemented as a four-phase pipeline, SkCC effectively reduces adaptation complexity from $O(m \\times n)$ to $O(m + n)$ across $m$ skills and $n$ frameworks. Experiments on SkillsBench demonstrate that SkCC delivers consistent and substantial gains over original counterparts, with pass rate increases from 21.1% to 33.3% on Claude Code and from 35.1% to 48.7% on Kimi CLI. Further, the design achieves sub-10ms compilation latency, 94.8% proactive security trigger rate, and 10-46% runtime token savings across frameworks.",
>         "doi": "",
>         "published_date": "2026-05-05T04:15:48",
>         "pdf_url": "https://arxiv.org/pdf/2605.03353v4",
>         "url": "http://arxiv.org/abs/2605.03353v4",
>         "source": "arxiv",
>         "updated_date": "2026-06-03T02:50:21",
>         "categories": "cs.CR; cs.AI",
>         "keywords": "",
>         "citations": 0,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "2604.10674v1",
>         "title": "Skill-SD: Skill-Conditioned Self-Distillation for Multi-turn LLM Agents",
>         "authors": "Hao Wang; Guozhi Wang; Han Xiao; Yufeng Zhou; Yue Pan; Jichao Wang; Ke Xu; Yafei Wen; Xiaohu Ruan; Xiaoxin Chen; Honggang Qi",
>         "abstract": "Reinforcement learning (RL) has been widely used to train LLM agents for multi-turn interactive tasks, but its sample efficiency is severely limited by sparse rewards and long horizons. On-policy self-distillation (OPSD) alleviates this by providing dense token-level supervision from a privileged teacher that has access to ground-truth answers. However, such fixed privileged information cannot capture the diverse valid strategies in agent tasks, and naively combining OPSD with RL often leads to training collapse. To address these limitations, we introduce Skill-SD, a framework that turns the agent's own trajectories into dynamic training-only supervision. Completed trajectories are summarized into compact natural language skills that describe successful behaviors, mistakes, and workflows. These skills serve as dynamic privileged information conditioning only the teacher, while the student always acts under the plain task prompt and learns to internalize the guidance through distillation. To stabilize the training, we derive an importance-weighted reverse-KL loss to provide gradient-correct token-level distillation, and dynamically synchronize the teacher with the improving student. Experimental results on agentic benchmarks demonstrate that Skill-SD substantially outperforms the standard RL baseline, improving both vanilla GRPO (+14.0%/+10.9% on AppWorld/Sokoban) and vanilla OPD (+42.1%/+40.6%). Project page: https://k1xe.github.io/skill-sd/",
>         "doi": "",
>         "published_date": "2026-04-12T14:57:52",
>         "pdf_url": "https://arxiv.org/pdf/2604.10674v1",
>         "url": "http://arxiv.org/abs/2604.10674v1",
>         "source": "arxiv",
>         "updated_date": "2026-04-12T14:57:52",
>         "categories": "cs.LG; cs.AI; cs.CL",
>         "keywords": "",
>         "citations": 0,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "W4393065402",
>         "title": "A survey on large language model based autonomous agents",
>         "authors": "Lei Wang; Chen Ma; Xueyang Feng; Zeyu Zhang; Hao Yang; Jingsen Zhang; Zhiyuan Chen; Jiakai Tang; Xu Chen; Yankai Lin; Wayne Xin Zhao; Zhewei Wei; Ji-Rong Wen",
>         "abstract": "Abstract Autonomous agents have long been a research focus in academic and industry communities. Previous research often focuses on training agents with limited knowledge within isolated environments, which diverges significantly from human learning processes, and makes the agents hard to achieve human-like decisions. Recently, through the acquisition of vast amounts of Web knowledge, large language models (LLMs) have shown potential in human-level intelligence, leading to a surge in research on LLM-based autonomous agents. In this paper, we present a comprehensive survey of these studies, delivering a systematic review of LLM-based autonomous agents from a holistic perspective. We first discuss the construction of LLM-based autonomous agents, proposing a unified framework that encompasses much of previous work. Then, we present a overview of the diverse applications of LLM-based autonomous agents in social science, natural science, and engineering. Finally, we delve into the evaluation strategies commonly used for LLM-based autonomous agents. Based on the previous studies, we also present several challenges and future directions in this field.",
>         "doi": "10.1007/s11704-024-40231-1",
>         "published_date": "2024-03-22T00:00:00",
>         "pdf_url": "https://link.springer.com/content/pdf/10.1007/s11704-024-40231-1.pdf",
>         "url": "https://doi.org/10.1007/s11704-024-40231-1",
>         "source": "openalex",
>         "updated_date": "",
>         "categories": "Computer science; Artificial intelligence",
>         "keywords": "",
>         "citations": 1108,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "W4405185373",
>         "title": "A review of large language models and autonomous agents in chemistry",
>         "authors": "Mayk Caldas Ramos; Christopher J. Collison; Andrew Dickson White",
>         "abstract": "Large language models (LLMs) have emerged as powerful tools in chemistry, significantly impacting molecule design, property prediction, and synthesis optimization. This review highlights LLM capabilities in these domains and their potential to accelerate scientific discovery through automation. We also review LLM-based autonomous agents: LLMs with a broader set of tools to interact with their surrounding environment. These agents perform diverse tasks such as paper scraping, interfacing with automated laboratories, and synthesis planning. As agents are an emerging topic, we extend the scope of our review of agents beyond chemistry and discuss across any scientific domains. This review covers the recent history, current capabilities, and design of LLMs and autonomous agents, addressing specific challenges, opportunities, and future directions in chemistry. Key challenges include data quality and integration, model interpretability, and the need for standard benchmarks, while future directions point towards more sophisticated multi-modal agents and enhanced collaboration between agents and experimental methods. Due to the quick pace of this field, a repository has been built to keep track of the latest studies: https://github.com/ur-whitelab/LLMs-in-science.",
>         "doi": "10.1039/d4sc03921a",
>         "published_date": "2024-12-09T00:00:00",
>         "pdf_url": "https://doi.org/10.1039/d4sc03921a",
>         "url": "https://doi.org/10.1039/d4sc03921a",
>         "source": "openalex",
>         "updated_date": "",
>         "categories": "Chemistry; Computer science; Biochemical engineering; Cognitive science; Psychology",
>         "keywords": "",
>         "citations": 186,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "W4362515116",
>         "title": "A Survey of Large Language Models",
>         "authors": "Wayne Xin Zhao; Kun Zhou; Junyi Li; Tianyi Tang; Xiaolei Wang; Yupeng Hou; Yingqian Min; Beichen Zhang; Junjie Zhang; Zican Dong; Yifan Du; Yang Chen; Yushuo Chen; Zhipeng Chen; Jinhao Jiang; Ruiyang Ren; Yifan Li; Xinyu Tang; Zikang Liu; Peiyu Liu; Jian‐Yun Nie; Ji-Rong Wen; Ji-Rong Wen",
>         "abstract": "Abstract The rapid evolution of large language models (LLMs) has driven a transformative shift in artificial intelligence (AI), reshaping both research paradigms and practical applications. Distinguished from their predecessors by unprecedented scale and advanced capabilities, LLMs necessitate new frameworks for understanding their development, behavior, and societal impact. This survey systematically reviews recent advancements in LLM techniques across four key dimensions: (1) pre-training methodologies, which establish core model capabilities through large-scale self-supervised training, architectural innovations, and data curation strategies; (2) post-training techniques, including supervised fine-tuning and reinforcement learning, which adapt foundational models to downstream tasks and enhance their alignment and safety; (3) utilization strategies, such as in-context learning, prompt engineering, and agentic reasoning, that optimize real-world deployment and enable effective interaction with external environments; and (4) evaluation methods, encompassing benchmarks for key ability dimensions such as core language capabilities, reasoning, and safety, which support comprehensive and reliable assessment of model performance. Additionally, we identify critical research issues, including those concerning theoretical foundations, efficient scaling, alignment, and agentic capability, and highlight the open challenges they present. By synthesizing state-of-the-art insights and emerging trends, this survey aims to provide a systematic and comprehensive framework for understanding the trajectory, current limitations, and future directions of LLM progress.",
>         "doi": "10.1007/s11704-026-60308-3",
>         "published_date": "2026-05-09T00:00:00",
>         "pdf_url": "https://link.springer.com/content/pdf/10.1007/s11704-026-60308-3.pdf",
>         "url": "https://doi.org/10.1007/s11704-026-60308-3",
>         "source": "openalex",
>         "updated_date": "",
>         "categories": "Language model; Computer science; Mainstream; Scale (ratio); Artificial intelligence",
>         "keywords": "",
>         "citations": 1401,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "W4386184788",
>         "title": "A Survey on Large Language Model based Autonomous Agents",
>         "authors": "Lei Wang; Chen Ma; Xueyang Feng; Zeyu Zhang; Hao Yang; Jingsen Zhang; Zhiyuan Chen; Jiakai Tang; Xu Chen; Yankai Lin; Wayne Xin Zhao; Zhewei Wei; Ji-Rong Wen",
>         "abstract": "Autonomous agents have long been a prominent research focus in both academic and industry communities. Previous research in this field often focuses on training agents with limited knowledge within isolated environments, which diverges significantly from human learning processes, and thus makes the agents hard to achieve human-like decisions. Recently, through the acquisition of vast amounts of web knowledge, large language models (LLMs) have demonstrated remarkable potential in achieving human-level intelligence. This has sparked an upsurge in studies investigating LLM-based autonomous agents. In this paper, we present a comprehensive survey of these studies, delivering a systematic review of the field of LLM-based autonomous agents from a holistic perspective. More specifically, we first discuss the construction of LLM-based autonomous agents, for which we propose a unified framework that encompasses a majority of the previous work. Then, we present a comprehensive overview of the diverse applications of LLM-based autonomous agents in the fields of social science, natural science, and engineering. Finally, we delve into the evaluation strategies commonly used for LLM-based autonomous agents. Based on the previous studies, we also present several challenges and future directions in this field. To keep track of this field and continuously update our survey, we maintain a repository of relevant references at https://github.com/Paitesanshi/LLM-Agent-Survey.",
>         "doi": "10.48550/arxiv.2308.11432",
>         "published_date": "2023-08-22T00:00:00",
>         "pdf_url": "https://arxiv.org/pdf/2308.11432",
>         "url": "http://arxiv.org/abs/2308.11432",
>         "source": "openalex",
>         "updated_date": "",
>         "categories": "Field (mathematics); Computer science; Data science; Autonomous agent; Perspective (graphical)",
>         "keywords": "",
>         "citations": 113,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "W4382656966",
>         "title": "GPT-3-Driven Pedagogical Agents to Train Children’s Curious Question-Asking Skills",
>         "authors": "Rania Abdelghani; Yen-Hsiang Wang; Xingdi Yuan; Tong Wang; Pauline Lucas; Hélène Sauzeon; Pierre-Yves Oudeyer",
>         "abstract": "",
>         "doi": "10.1007/s40593-023-00340-7",
>         "published_date": "2023-06-30T00:00:00",
>         "pdf_url": "https://hal.science/hal-03890447/document",
>         "url": "https://doi.org/10.1007/s40593-023-00340-7",
>         "source": "openalex",
>         "updated_date": "",
>         "categories": "Mathematics education; Educational technology; Computer science; Multimedia; Psychology",
>         "keywords": "",
>         "citations": 109,
>         "references": "",
>         "extra": ""
>       },
>       {
>         "paper_id": "10.65109/fmju3241",
>         "title": "Hierarchical Cooperative Multi-Agent Reinforcement Learning with Skill Discovery",
>         "authors": "Jiachen Yang; Igor Borovikov; Hongyuan Zha",
>         "abstract": "<jats:p>Human players in professional team sports achieve high level coordination by dynamically choosing complementary skills and executing primitive actions to perform these skills. As a step toward creating intelligent agents with this capability for fully cooperative multi-agent settings, we propose a two-level hierarchical multi-agent reinforcement learning (MARL) algorithm with unsupervised skill discovery. Agents learn useful and distinct skills at the low level via independent Q-learning, while they learn to select complementary latent skill variables at the high level via centralized multi-agent training with an extrinsic team reward. The set of low-level skills emerges from an intrinsic reward that solely promotes the decodability of latent skill variables from the trajectory of a low-level skill, without the need for hand-crafted rewards for each skill. For scalable decentralized execution, each agent independently chooses latent skill variables and primitive actions based on local observations. Our overall method enables the use of general cooperative MARL algorithms for training high level policies and single-agent RL for training low level skills. Experiments on a stochastic high dimensional team game show the emergence of useful skills and cooperative team play. The interpretability of the learned skills show the promise of the proposed method for achieving human-AI cooperation in team sports games.</jats:p>",
>         "doi": "10.65109/fmju3241",
>         "published_date": "2020-05-05T00:00:00",
>         "pdf_url": "",
>         "url": "https://doi.org/10.65109/fmju3241",
>         "source": "crossref",
>         "updated_date": "",
>         "categories": "proceedings-article",
>         "keywords": "",
>         "citations": 4,
>         "references": "",
>         "extra": "{'publisher': 'IEEE Computer Society', 'container_title': 'International Joint Conference on Autonomous Agents and Multiagent Systems', 'volume': '', 'issue': '', 'page': '1566-1574', 'issn': ['1558-2914'], 'isbn': [], 'crossref_type': 'proceedings-article', 'member': '54598', 'prefix': '10.65109'}"
>       },
>       {
>         "paper_id": "10.65109/brag3288",
>         "title": "IntentCUA: Learning Intent-level Representations for Skill Abstraction and Multi-Agent Planning in Computer-Use Agents",
>         "authors": "Seoyoung Lee; Seobin Yoon; Seongbeen Lee; Yoojung Chun; Dayoung Park; Doyeon Kim; Joo Yong Sim",
>         "abstract": "",
>         "doi": "10.65109/brag3288",
>         "published_date": "1970-01-01T00:00:00",
>         "pdf_url": "",
>         "url": "https://doi.org/10.65109/brag3288",
>         "source": "crossref",
>         "updated_date": "",
>         "categories": "proceedings-article",
>         "keywords": "",
>         "citations": 0,
>         "references": "",
>         "extra": "{'publisher': 'International Foundation for Autonomous Agents and Multiagent Systems', 'container_title': 'Proceedings of the 25th International Conference on Autonomous Agents and Multiagent Systems', 'volume': '', 'issue': '', 'page': '', 'issn': [], 'isbn': [], 'crossref_type': 'proceedings-article', 'member': '54598', 'prefix': '10.65109'}"
>       },
>       {
>         "paper_id": "10.2139/ssrn.6438922",
>         "title": "Beyond Prescribed Intelligence: Toward Self-Directed Skill Acquisition in LLM-Based Cybersecurity Agents",
>         "authors": "Mika D Ayenson",
>         "abstract": "<jats:p>The dominant paradigm for integrating Large Language Models (LLMs) into Security Operations Centers (SOCs), which this paper terms prescribed intelligence (human-engineered skills, hardcoded tool integrations, and prompt templates), is structurally insufficient for replicating the adaptive workflows of expert SOC analysts. This paper identifies five limitations of that paradigm and proposes credentialed self-exploration as an alternative: provision an LLM agent with strictly read-only access to a sandboxed or test SOC environment, describe its role, and let it autonomously discover data sources, learn tool relationships, and propose executable skills for human review. During exploration, the agent takes no action on production systems; only skills that pass human review are deployed. The paper introduces AESOP (Agent-driven Exploration for Security Operations Proficiency), a conceptual framework for this self-directed architecture, and outlines a research agenda for empirical validation. Supporting evidence draws on cross-domain advances in tool learning, autonomous agents, and offensive security (penetration testing), demonstrating patterns transferable to defensive SOC operations-a central question this work raises.</jats:p>",
>         "doi": "10.2139/ssrn.6438922",
>         "published_date": "2026-01-01T00:00:00",
>         "pdf_url": "",
>         "url": "https://doi.org/10.2139/ssrn.6438922",
>         "source": "crossref",
>         "updated_date": "",
>         "categories": "posted-content",
>         "keywords": "",
>         "citations": 0,
>         "references": "",
>         "extra": "{'publisher': 'Elsevier BV', 'container_title': '', 'volume': '', 'issue': '', 'page': '', 'issn': [], 'isbn': [], 'crossref_type': 'posted-content', 'member': '78', 'prefix': '10.2139'}"
>       },
>       {
>         "paper_id": "10.65109/xuim2827",
>         "title": "Autonomous Skill Acquisition for Robots Using Graduated Learning",
>         "authors": "Gautham Vasan",
>         "abstract": "<jats:p>Skill acquisition is among the most remarkable aspects of human intelligence. It involves discovering purposeful behavioural modules, retaining them as skills, honing them through practice, and applying them in unforeseen circumstances [11]. Skill acquisition underlies our ability to choose to spend time and energy on the mastery of particular tasks and draw upon previous experience to solve more complex problems over time with less cognitive effort[10]. If endowed with continual skill acquisition, robots can autonomously improve their skills over time, where learning at one stage of development is a foundation for future learning [23]. It could unlock new possibilities for physical automation with general-purpose robots, just as general-purpose computer processors ushered in the information age [24, 33]. In this work, we propose a novel approach called Graduated Learning, where we ask a robot to acquire new manipulation and locomotion skills repeatedly, using time-delineated experiences of attempts at those skills (i.e., episodes) and some store of previously acquired knowledge (e.g., weights of a neural network). Our proposed approach chooses the order in which an agent learns these skills since the progressive manner in which they are developed plays a vital role in developing a final skill set.</jats:p>",
>         "doi": "10.65109/xuim2827",
>         "published_date": "2024-05-06T00:00:00",
>         "pdf_url": "",
>         "url": "https://doi.org/10.65109/xuim2827",
>         "source": "crossref",
>         "updated_date": "",
>         "categories": "proceedings-article",
>         "keywords": "",
>         "citations": 0,
>         "references": "",
>         "extra": "{'publisher': 'IEEE Computer Society', 'container_title': 'International Joint Conference on Autonomous Agents and Multiagent Systems', 'volume': '', 'issue': '', 'page': '2785-2787', 'issn': ['1558-2914'], 'isbn': [], 'crossref_type': 'proceedings-article', 'member': '54598', 'prefix': '10.65109'}"
>       },
>       {
>         "paper_id": "10.2139/ssrn.4790564",
>         "title": "Skill Matters: Dynamic Skill Learning for Multi-Agent Cooperative Reinforcement Learning",
>         "authors": "Tong Li; Chenjia Bai; Kang Xu; Chen Chu; Peican Zhu; Zhen Wang",
>         "abstract": "",
>         "doi": "10.2139/ssrn.4790564",
>         "published_date": "2024-01-01T00:00:00",
>         "pdf_url": "",
>         "url": "https://doi.org/10.2139/ssrn.4790564",
>         "source": "crossref",
>         "updated_date": "",
>         "categories": "posted-content",
>         "keywords": "",
>         "citations": 0,
>         "references": "",
>         "extra": "{'publisher': 'Elsevier BV', 'container_title': '', 'volume': '', 'issue': '', 'page': '', 'issn': [], 'isbn': [], 'crossref_type': 'posted-content', 'member': '78', 'prefix': '10.2139'}"
>       }
>     ],
>     "total": 15,
>     "raw_total": 15
>   }
> }
> ```
>
> 检索结果里有几篇非常贴题：有偏“SKILL markdown/技能库工程化”的，也有偏“Agent 自主发现技能”的。我会选三篇覆盖面比较好的：技能库重构、跨框架技能编译、安全/可迁移，以及自主技能发现。
>
> 我已在仓库里建好 `papers/agent-skills` 目录，用来放这三篇 PDF。现在开始通过 MCP 下载，下载后我会检查文件是否落盘成功。
>
> <details><summary>Called 3 tools</summary>
>
> MCP tool call
>
> paper-search-mcp.download_arxiv
>
> ```json
> {
>   "paper_id": "2605.27955",
>   "save_path": "c:\\code\\paper-search-mcp\\papers\\agent-skills"
> }
> ```
>
> ```text
> Error executing tool download_arxiv: HTTPSConnectionPool(host='arxiv.org', port=443): Max retries exceeded with url: /pdf/2605.27955.pdf (Caused by ConnectTimeoutError(<HTTPSConnection(host='arxiv.org', port=443) at 0x252e315a560>, 'Connection to arxiv.org timed out. (connect timeout=None)'))
> ```
>
> MCP tool call
>
> paper-search-mcp.download_arxiv
>
> ```json
> {
>   "paper_id": "2605.03353",
>   "save_path": "c:\\code\\paper-search-mcp\\papers\\agent-skills"
> }
> ```
>
> ```text
> {
>   "status": "downloaded",
>   "pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.03353.pdf",
>   "pdf_paths": [
>     "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.03353.pdf"
>   ],
>   "source": "arxiv",
>   "paper_id": "2605.03353",
>   "doi": "",
>   "title": "2605.03353",
>   "parse_prompt": {
>     "status": "elicitation_not_accepted",
>     "interaction": "backend_session_numbered_selection",
>     "selection_token": "search_20260610_115810_ff251479",
>     "instructions": "PDF saved. Present the numbered papers to the user. To parse selected PDFs, call parse_selected_papers(selection_token=<token>, selected_indices='1') or selected_indices='all'.",
>     "papers": [
>       {
>         "index": 1,
>         "title": "2605.03353",
>         "authors": "",
>         "year": "",
>         "source": "arxiv",
>         "paper_id": "2605.03353",
>         "doi": "",
>         "pdf_url": "",
>         "local_pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.03353.pdf",
>         "url": "",
>         "parse_ready": true,
>         "reason": "local_pdf_path"
>       }
>     ],
>     "total": 1,
>     "parse_ready_total": 1,
>     "elicitation_action": "decline",
>     "message": "User declined or cancelled parsing. Use parse_selected_papers with numbered indices if needed."
>   }
> }
> ```
>
> ```json
> {
>   "result": {
>     "status": "downloaded",
>     "pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.03353.pdf",
>     "pdf_paths": [
>       "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.03353.pdf"
>     ],
>     "source": "arxiv",
>     "paper_id": "2605.03353",
>     "doi": "",
>     "title": "2605.03353",
>     "parse_prompt": {
>       "status": "elicitation_not_accepted",
>       "interaction": "backend_session_numbered_selection",
>       "selection_token": "search_20260610_115810_ff251479",
>       "instructions": "PDF saved. Present the numbered papers to the user. To parse selected PDFs, call parse_selected_papers(selection_token=<token>, selected_indices='1') or selected_indices='all'.",
>       "papers": [
>         {
>           "index": 1,
>           "title": "2605.03353",
>           "authors": "",
>           "year": "",
>           "source": "arxiv",
>           "paper_id": "2605.03353",
>           "doi": "",
>           "pdf_url": "",
>           "local_pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.03353.pdf",
>           "url": "",
>           "parse_ready": true,
>           "reason": "local_pdf_path"
>         }
>       ],
>       "total": 1,
>       "parse_ready_total": 1,
>       "elicitation_action": "decline",
>       "message": "User declined or cancelled parsing. Use parse_selected_papers with numbered indices if needed."
>     }
>   }
> }
> ```
>
> MCP tool call
>
> paper-search-mcp.download_arxiv
>
> ```json
> {
>   "paper_id": "2405.15019",
>   "save_path": "c:\\code\\paper-search-mcp\\papers\\agent-skills"
> }
> ```
>
> ```text
> {
>   "status": "downloaded",
>   "pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2405.15019.pdf",
>   "pdf_paths": [
>     "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2405.15019.pdf"
>   ],
>   "source": "arxiv",
>   "paper_id": "2405.15019",
>   "doi": "",
>   "title": "2405.15019",
>   "parse_prompt": {
>     "status": "elicitation_not_accepted",
>     "interaction": "backend_session_numbered_selection",
>     "selection_token": "search_20260610_115811_87148999",
>     "instructions": "PDF saved. Present the numbered papers to the user. To parse selected PDFs, call parse_selected_papers(selection_token=<token>, selected_indices='1') or selected_indices='all'.",
>     "papers": [
>       {
>         "index": 1,
>         "title": "2405.15019",
>         "authors": "",
>         "year": "",
>         "source": "arxiv",
>         "paper_id": "2405.15019",
>         "doi": "",
>         "pdf_url": "",
>         "local_pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2405.15019.pdf",
>         "url": "",
>         "parse_ready": true,
>         "reason": "local_pdf_path"
>       }
>     ],
>     "total": 1,
>     "parse_ready_total": 1,
>     "elicitation_action": "decline",
>     "message": "User declined or cancelled parsing. Use parse_selected_papers with numbered indices if needed."
>   }
> }
> ```
>
> ```json
> {
>   "result": {
>     "status": "downloaded",
>     "pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2405.15019.pdf",
>     "pdf_paths": [
>       "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2405.15019.pdf"
>     ],
>     "source": "arxiv",
>     "paper_id": "2405.15019",
>     "doi": "",
>     "title": "2405.15019",
>     "parse_prompt": {
>       "status": "elicitation_not_accepted",
>       "interaction": "backend_session_numbered_selection",
>       "selection_token": "search_20260610_115811_87148999",
>       "instructions": "PDF saved. Present the numbered papers to the user. To parse selected PDFs, call parse_selected_papers(selection_token=<token>, selected_indices='1') or selected_indices='all'.",
>       "papers": [
>         {
>           "index": 1,
>           "title": "2405.15019",
>           "authors": "",
>           "year": "",
>           "source": "arxiv",
>           "paper_id": "2405.15019",
>           "doi": "",
>           "pdf_url": "",
>           "local_pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2405.15019.pdf",
>           "url": "",
>           "parse_ready": true,
>           "reason": "local_pdf_path"
>         }
>       ],
>       "total": 1,
>       "parse_ready_total": 1,
>       "elicitation_action": "decline",
>       "message": "User declined or cancelled parsing. Use parse_selected_papers with numbered indices if needed."
>     }
>   }
> }
> ```
>
> </details>
>
> 两篇已经顺利下载；`Skill-as-Pseudocode` 这篇第一次连 arXiv 超时了，像是临时网络抖动。我先重试它，如果还不行就换同样贴题的 `Skill-SD` 作为第三篇。
>
> MCP tool call
>
> paper-search-mcp.download_arxiv
>
> ```json
> {
>   "paper_id": "2605.27955v1",
>   "save_path": "c:\\code\\paper-search-mcp\\papers\\agent-skills"
> }
> ```
>
> ```text
> {
>   "status": "downloaded",
>   "pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.27955v1.pdf",
>   "pdf_paths": [
>     "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.27955v1.pdf"
>   ],
>   "source": "arxiv",
>   "paper_id": "2605.27955v1",
>   "doi": "",
>   "title": "2605.27955v1",
>   "parse_prompt": {
>     "status": "elicitation_not_accepted",
>     "interaction": "backend_session_numbered_selection",
>     "selection_token": "search_20260610_115824_284d997a",
>     "instructions": "PDF saved. Present the numbered papers to the user. To parse selected PDFs, call parse_selected_papers(selection_token=<token>, selected_indices='1') or selected_indices='all'.",
>     "papers": [
>       {
>         "index": 1,
>         "title": "2605.27955v1",
>         "authors": "",
>         "year": "",
>         "source": "arxiv",
>         "paper_id": "2605.27955v1",
>         "doi": "",
>         "pdf_url": "",
>         "local_pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.27955v1.pdf",
>         "url": "",
>         "parse_ready": true,
>         "reason": "local_pdf_path"
>       }
>     ],
>     "total": 1,
>     "parse_ready_total": 1,
>     "elicitation_action": "decline",
>     "message": "User declined or cancelled parsing. Use parse_selected_papers with numbered indices if needed."
>   }
> }
> ```
>
> ```json
> {
>   "result": {
>     "status": "downloaded",
>     "pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.27955v1.pdf",
>     "pdf_paths": [
>       "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.27955v1.pdf"
>     ],
>     "source": "arxiv",
>     "paper_id": "2605.27955v1",
>     "doi": "",
>     "title": "2605.27955v1",
>     "parse_prompt": {
>       "status": "elicitation_not_accepted",
>       "interaction": "backend_session_numbered_selection",
>       "selection_token": "search_20260610_115824_284d997a",
>       "instructions": "PDF saved. Present the numbered papers to the user. To parse selected PDFs, call parse_selected_papers(selection_token=<token>, selected_indices='1') or selected_indices='all'.",
>       "papers": [
>         {
>           "index": 1,
>           "title": "2605.27955v1",
>           "authors": "",
>           "year": "",
>           "source": "arxiv",
>           "paper_id": "2605.27955v1",
>           "doi": "",
>           "pdf_url": "",
>           "local_pdf_path": "C:\\code\\paper-search-mcp\\papers\\agent-skills\\2605.27955v1.pdf",
>           "url": "",
>           "parse_ready": true,
>           "reason": "local_pdf_path"
>         }
>       ],
>       "total": 1,
>       "parse_ready_total": 1,
>       "elicitation_action": "decline",
>       "message": "User declined or cancelled parsing. Use parse_selected_papers with numbered indices if needed."
>     }
>   }
> }
> ```

</details>

已用 MCP 工具爬取并下载 3 篇 agent skill 相关论文，放在：

`C:\code\paper-search-mcp\papers\agent-skills`

1. **Skill-as-Pseudocode: Refactoring Skill Libraries to Pseudocode for LLM Agents**
   arXiv: 2605.27955v1
   本地文件：[2605.27955v1.pdf](./papers/agent-skills/2605.27955v1.pdf)

2. **SkCC: Portable and Secure Skill Compilation for Cross-Framework LLM Agents**
   arXiv: 2605.03353v4
   本地文件：[2605.03353.pdf](./papers/agent-skills/2605.03353.pdf)

3. **Agentic Skill Discovery**
   arXiv: 2405.15019v2
   本地文件：[2405.15019.pdf](./papers/agent-skills/2405.15019.pdf)

其中第一篇一开始 arXiv 连接超时，重试后已经成功下载。

> 1.当前我没有指定保存位置，为什么默认给我保存到 C:\code\paper-search-mcp\papers\agent-skills，我设置的默认保存位置应该是~desktop 2.为什么发生了论文保存操作，但是没有触发 checkbox 来选择是否进行 minerU 解析？

<details><summary>4 previous messages</summary>

> 你问得对，这里有两个层面的行为混在了一起：一个是我这次调用时人为传了保存路径，另一个是 MCP 后端返回了“可解析提示”，但当前 Codex 工具界面没有把它渲染成交互 checkbox。我先核一下仓库里相关实现，给你一个准确解释。
>
> 我从 README 和测试里已经看到项目设计：默认确实是 `~/Desktop`，并且保存 PDF 后会返回 `parse_prompt`。我再看一下 `server.py` 的实际分支，特别是“有无 elicitation context 时怎么降级”。
>
> 关键点已经比较清楚：不是项目默认值变了，而是我上一步为了把三篇集中放一起，显式传了 `save_path`；这会覆盖 MCP 的默认 `~/Desktop`。checkbox 这边则是客户端交互能力/当前工具桥接的问题，不是 PDF 保存逻辑没有返回解析提示。
>
> 我再补一个细节：刚才的工具返回不是完全没有提示，而是返回了 `parse_prompt`，里面是 `status: elicitation_not_accepted` 和一个 `selection_token`。这说明后端已经尝试进入“保存后解析选择”流程，但当前客户端这层没有把 MCP elicitation 展示成可勾选 UI，而是自动走了取消/降级路径。

</details>

你说得对，这次不是 MCP 默认位置生效，而是**我在调用下载工具时显式传了**：

`save_path="c:\\code\\paper-search-mcp\\papers\\agent-skills"`

所以覆盖了项目默认的 `~/Desktop`。代码里的默认值确实还是 `~/Desktop`，见 [utils.py](./paper_search_mcp/utils.py:5) 和 [server.py](./paper_search_mcp/server.py:1399)。这是我为了把三篇论文集中放到仓库子目录里做的假设，应该先说明，抱歉。

第二个问题：保存 PDF 后其实**触发了 parse prompt**，但没有显示成 checkbox。原因是当前 Codex 这层 MCP 调用没有把 server 的 `ctx.elicit(...)` 渲染成可见的多选 UI，后端就返回了降级结果：`interaction: backend_session_numbered_selection`，并带了 `selection_token`。相关逻辑在 [server.py](./paper_search_mcp/server.py:550)：如果客户端不支持或没有接受 elicitation，就返回编号选择流程，而不是 checkbox。

也就是说：

- 默认保存位置没错，是我覆盖了。
- checkbox 不是没触发保存后解析逻辑，而是当前客户端没有展示 MCP elicitation UI，结果降级成了 `selection_token + 编号选择`。
- 要强制解析这些已下载 PDF，可以直接调用 `parse_pdf_with_mineru`，或用返回的 `selection_token` 调 `parse_selected_papers`。以后我会在你没指定保存位置时不再擅自传 `save_path`。

> 1.请你进行代码约束，不能擅自传 save_path 2.客户端没有展示 MCP elicitation UI，结果降级成了 selection_token + 编号选择，这个问题应该如何解决

<details><summary>39 previous messages</summary>

> 我会把这个拆成两块处理：先在代码层加一个“保存路径覆盖策略”，让工具默认只能用 `~/Desktop`，除非你显式开启；然后梳理 elicitation 降级的根因，并把可操作的解决路径写清楚。先读一下当前配置和 server 参数组织，避免改得太莽。
>
> <details><summary>Ran 3 commands</summary>
>
> - Ran `Get-Content -LiteralPath .\paper_search_mcp\config.py`
> - Ran `rg -n "resolve_save_path\(|save_path = resolve_save_path|DEFAULT_SAVE_PATH|save_path: str = DEFAULT_SAVE_PATH|save_path=DEFAULT_SAVE_PATH" .\paper_search_mcp .\tests .\README*.md .\.env.example`
> - Ran `$i=0; Get-Content -LiteralPath .\paper_search_mcp\server.py | ForEach-Object { $i++; if($i -ge 1 -and $i -le 130){ '{0,4}: {1}' -f $i, $_ } }`
>
> </details>
>
> 我准备加的是“默认拒绝自定义保存路径”的硬约束，而不是单纯靠提示词约束模型行为。也就是：MCP 工具如果收到非 `~/Desktop` 的 `save_path`，默认直接返回错误；只有你在 `.env` 里显式打开开关时才允许覆盖。
>
> <details><summary>Ran 4 commands</summary>
>
> - Ran `Get-Content -LiteralPath .\tests\test_selection_sessions.py | Select-Object -First 140`
> - Ran `Get-Content -LiteralPath .\tests\test_server.py | Select-Object -First 130`
> - Ran `$i=0; Get-Content -LiteralPath .\paper_search_mcp\server.py | ForEach-Object { $i++; if($i -ge 1170 -and $i -le 1238){ '{0,4}: {1}' -f $i, $_ } }`
> - Ran `$i=0; Get-Content -LiteralPath .\paper_search_mcp\server.py | ForEach-Object { $i++; if($i -ge 700 -and $i -le 775){ '{0,4}: {1}' -f $i, $_ } }`
>
> </details>
>
> 我已经看完主要保存路径链路。实现上我会把约束放在 MCP server 层，而不是改每个搜索源 connector，这样 CLI 仍然可以保留 `-o` 的正常能力；MCP 工具默认只允许 `~/Desktop`，这正好防住我这种代理层误传参数。
>
> <details><summary>Ran 4 commands</summary>
>
> - Ran `$i=0; Get-Content -LiteralPath .\paper_search_mcp\server.py | ForEach-Object { $i++; if($i -ge 1788 -and $i -le 1820){ '{0,4}: {1}' -f $i, $_ } }`
> - Ran `$i=0; Get-Content -LiteralPath .\paper_search_mcp\server.py | ForEach-Object { $i++; if($i -ge 1918 -and $i -le 2090){ '{0,4}: {1}' -f $i, $_ } }`
> - Ran `Get-Content -LiteralPath .\.env.example | Select-Object -First 80`
> - Ran `$i=0; Get-Content -LiteralPath .\paper_search_mcp\server.py | ForEach-Object { $i++; if($i -ge 2238 -and $i -le 2275){ '{0,4}: {1}' -f $i, $_ } }`
>
> </details>
>
> 我现在动手改文件：会新增一个 `enforce_mcp_save_path` 之类的 helper，并把 MCP 下载/解析下载链路统一接到这个 helper。这样即使代理误传 `save_path`，服务端也会拦下来。
>
> <details><summary>Ran 3 commands</summary>
>
> - Ran `Get-Content -LiteralPath .\pyproject.toml`
> - Ran `rg -n "def test_.*save_path|ALLOW_CUSTOM|custom save|download_with_fallback|parse_downloaded_paper|download_scihub" .\tests .\paper_search_mcp`
> - Ran `git status --short`
>
> </details>
>
> 我看到当前工作区已有 `README.md` / `README_CN.md` 修改和 `papers/` 目录未跟踪，后者是我刚才下载造成的；README 我先不碰，避免覆盖你正在看的内容。代码约束我会集中改 `server.py`、`utils.py` 和测试，文档部分最后只给你说明，不直接动你已有改动。
>
> 我会把“是否允许非默认保存目录”做成环境变量控制，默认关闭。这样用户和工具都能看到明确错误，而不是静默把文件写到别处；如果你以后确实要改保存目录，可以在 `.env` 里显式开启。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `rg -n "save_path=tmp|save_path = tmp|download_arxiv\(|download_with_fallback\(|parse_selected_papers\(|parse_downloaded_paper\(|search_papers_with_elicitation\(" .\tests`
> - Ran `Get-Content -LiteralPath .\tests\test_fallback.py`
>
> </details>
>
> 实现细节上我会让错误是结构化的 `status: invalid_save_path`，而不是让 MCP 工具直接抛异常中断。这样前端/代理可以明确知道“路径被服务端策略拦截”，也能提示去改 `.env`。
>
> <details><summary>Ran 3 commands</summary>
>
> - Ran `rg -n "arxiv_searcher\.download_pdf|download_pdf\(.*save_path|read_paper\(.*save_path|download_arxiv\(" .\tests`
> - Ran `$i=0; Get-Content -LiteralPath .\paper_search_mcp\server.py | ForEach-Object { $i++; if($i -ge 2130 -and $i -le 2215){ '{0,4}: {1}' -f $i, $_ } }`
> - Ran `$i=0; Get-Content -LiteralPath .\paper_search_mcp\server.py | ForEach-Object { $i++; if($i -ge 1028 -and $i -le 1162){ '{0,4}: {1}' -f $i, $_ } }`
>
> </details>
>
> 我会保持工具 schema 兼容，不删除 `save_path` 参数；删除参数会破坏现有调用方。真正的约束会在服务端执行：收到非默认路径就返回错误，除非 `PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH=true`。
>
> <details><summary>Edited 1 file</summary>
>
> - Wrote `./paper_search_mcp/server.py` (+33 -4)
>
> </details>
>
> 我已经加了策略 helper。下一步把它接到实际会写 PDF 的 MCP 边界：`download_*` / `read_*` 的共用路径、`parse_selected_papers`、`download_with_fallback`、`parse_downloaded_paper` 和 Sci-Hub 入口。
>
> <details><summary>Ran 1 command</summary>
>
> - Ran `rg -n "async def .*\(|save_path: str = DEFAULT_SAVE_PATH|return await _download_source_pdf|return await _read_source_paper|download_scihub|download_with_fallback|parse_downloaded_paper" .\paper_search_mcp\server.py`
>
> </details>
>
> 我继续把 guard 接入具体入口。这里会刻意放在“公共 MCP 工具边界”，内部测试和 connector 仍然可以使用临时目录；用户通过 MCP 调用时则被策略保护。
>
> <details><summary>Edited 1 file</summary>
>
> - Wrote `./paper_search_mcp/server.py` (+31 -22)
>
> </details>
>
> 公共的 `download/read` 路径已经接上 guard 了。现在我继续覆盖“先选编号再解析”和“下载后直接解析”这两条路径，否则它们仍可能通过 `save_path` 写到非默认目录。
>
> <details><summary>Edited 1 file</summary>
>
> - Wrote `./paper_search_mcp/server.py` (+48 -32)
>
> </details>
>
> 路径约束已经接入主要 MCP 写盘入口了。我现在补测试：一类验证默认拦截自定义目录，另一类验证显式环境变量打开后仍能使用临时目录，避免把测试和高级用法一起误伤。
>
> <details><summary>Ran 4 commands</summary>
>
> - Ran `git diff -- .\paper_search_mcp\server.py`
> - Ran `Get-Content -LiteralPath .\tests\test_selection_sessions.py | Select-Object -First 360`
> - Ran `Get-Content -LiteralPath .\tests\test_server.py | Select-Object -First 130`
> - Ran `Get-Content -LiteralPath .\tests\test_fallback.py`
>
> </details>
>
> 我刚才的改动已经到了服务端 guard 这一层；现在继续补测试。这里我会让旧测试里“故意用临时目录”的场景显式开启 `PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH=true`，这样测试意图和新策略一致。
>
> 同时我不会动你当前已有改动的 README 文件，避免把你打开的文档编辑覆盖掉。`.env.example` 我会加一个开关示例，因为这是新行为的入口。
>
> <details><summary>Edited 1 file</summary>
>
> - Wrote `./tests/test_selection_sessions.py` (+51 -12)
>
> </details>
>
> 测试这边还差一个旧集成测试：它直接传 `./downloads`，在新策略下会被正确拦截，所以我要么给它显式开关，要么改成验证默认路径。我会保留它原本“下载到测试目录”的语义，用环境变量显式开启。
>
> <details><summary>Edited 1 file</summary>
>
> - Wrote `./tests/test_server.py` (+21 -8)
>
> </details>
>
> 我刚补了两类测试：默认传自定义路径会被 `invalid_save_path` 拦截；测试或高级用户需要自定义目录时，必须显式设置 `PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH=true`。现在我会把 `.env.example` 加上这条注释，然后跑相关测试。
>
> <details><summary>Edited 1 file</summary>
>
> - Wrote `./.env.example` (+7 -4)
>
> </details>
>
> 代码改动已经落好，我现在跑针对性的单元测试，先避开那个会真实连 arXiv 下载 10 篇的旧网络测试，免得把验证变成网络波动抽奖。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `python -m unittest tests.test_selection_sessions tests.test_fallback tests.test_server.TestPaperSearchServer.test_download_arxiv_rejects_custom_save_path_by_default tests.test_server.TestPaperSearchServer.test_parse_pdf_with_mineru_pypdf_mode`
> - Ran `git diff --check`
>
> </details>
>
> 本地裸 `python` 环境里没有安装 `mcp` 依赖，测试没真正跑起来；这是环境问题，不是断言失败。我改用项目的 `uv run` 环境再跑一次，这样会按 `pyproject.toml/uv.lock` 拉起正确依赖。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `uv run python -m unittest tests.test_selection_sessions tests.test_fallback tests.test_server.TestPaperSearchServer.test_download_arxiv_rejects_custom_save_path_by_default tests.test_server.TestPaperSearchServer.test_parse_pdf_with_mineru_pypdf_mode`
> - Ran `git diff -- .\paper_search_mcp\server.py .\tests\test_selection_sessions.py .\tests\test_server.py .\.env.example`
>
> </details>
>
> 改动和测试都完成了。结果是：MCP 入口现在默认拒绝非 `~/Desktop` 的保存目录；如果确实要开放自定义目录，必须显式设置环境变量。接下来我给你一个清晰的收尾，包括 elicitation 这个问题的真实边界。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `$files=@('.\paper_search_mcp\server.py','.\tests\test_selection_sessions.py','.\tests\test_server.py','.\.env.example'); foreach($f in $files){ Select-String -LiteralPath $f -Pattern 'ALLOW_CUSTOM_SAVE_PATH|invalid_save_path|test_parse_selected_papers_rejects|test_download_arxiv_rejects' | ForEach-Object { '{0}:{1}: {2}' -f $_.Path,$_.LineNumber,$_.Line.Trim() } }`
> - Ran `git status --short`
>
> </details>
>
> 已完成代码约束：MCP 工具现在默认**拒绝非 `~/Desktop` 的 `save_path`**。如果代理或客户端再擅自传 `save_path`，服务端会直接返回 `status: invalid_save_path`，不会开始下载。
>
> 改动位置：
>
> - [server.py](./paper_search_mcp/server.py:59)：新增 `PAPER_SEARCH_MCP_ALLOW
>
> Stream error
>
> Reconnecting 1/5
> stream disconnected before completion: Upstream request failed
>
> <details><summary>Plan</summary>
>
> - [x] 定位 MCP 保存路径入口
> - [x] 实现默认禁止自定义 save_path 的服务端约束
> - [x] 补充测试覆盖允许/拒绝两种情况
> - [x] 运行相关测试并总结 elicitation 解决方案
>
> </details>

</details>

已完成代码约束：MCP 入口现在默认拒绝自定义 `save_path`，只能落到 `~/Desktop`。如果代理或工具调用擅自传入别的路径，会返回 `status: invalid_save_path`，不会开始下载。只有显式设置：

```env
PAPER_SEARCH_MCP_ALLOW_CUSTOM_SAVE_PATH=true
```

才允许覆盖保存目录。改动位置：

- [server.py](./paper_search_mcp/server.py:59)：新增保存路径策略开关与校验
- [server.py](./paper_search_mcp/server.py:745)：下载入口拦截自定义路径
- [server.py](./paper_search_mcp/server.py:1225)：`parse_selected_papers` 拦截自定义路径
- [.env.example](./.env.example:17)：新增配置示例
- [test_selection_sessions.py](./tests/test_selection_sessions.py:128) 和 [test_server.py](./tests/test_server.py:84)：补了默认拒绝测试

验证已通过：

```powershell
uv run python -m unittest tests.test_selection_sessions tests.test_fallback tests.test_server.TestPaperSearchServer.test_download_arxiv_rejects_custom_save_path_by_default tests.test_server.TestPaperSearchServer.test_parse_pdf_with_mineru_pypdf_mode
```

关于 checkbox / MCP elicitation：这个不是 server 单边能强制解决的问题。server 已经调用 `ctx.elicit(...)` 并准备了多选 schema；但 checkbox 是否显示取决于当前 MCP 客户端/桥接层是否支持并渲染 elicitation。当前 Codex 工具桥接没有展示这个 UI，所以会降级成 `selection_token + 编号选择`。

解决路径是：

1. 在支持 MCP Elicitation 的客户端里调用 `search_papers_with_elicitation`，并确认客户端版本支持多选表单。
2. 当前客户端不支持 UI 时，使用降级流程：拿 `selection_token`，再调用 `parse_selected_papers(selection_token=..., selected_indices="1,3")`。
3. 对“保存 PDF 后是否解析”的场景，后端已经返回 `parse_prompt`；客户端需要实现对 `ctx.elicit` 的 UI 渲染，才能真正出现 checkbox。

我没有改你已有修改的 `README.md` / `README_CN.md`，避免覆盖你打开的文档改动。

> 将默认保存路径修改为~/Desktop/papers

<details><summary>32 previous messages</summary>

> 我会把真正的默认源头改掉，也就是 `DEFAULT_SAVE_PATH`，这样所有 MCP/CLI 默认都会跟着变成 `~/Desktop/papers`；同时同步我上次加的 `.env.example` 注释，避免提示还写旧路径。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `rg -n "~/Desktop|DEFAULT_SAVE_PATH|Desktop/papers" .\paper_search_mcp .\.env.example .\tests`
> - Ran `git status --short`
>
> </details>
>
> 我会把默认值源头从 `~/Desktop` 改为 `~/Desktop/papers`，并同步 server 暴露给 MCP 的 docstring 文案。README 现在已有未归属修改，我先不碰它，避免把你的文档编辑混进去。
>
> <details><summary>Edited 1 file</summary>
>
> - Wrote `./paper_search_mcp/utils.py` (+3 -3)
>
> </details>
>
> 默认值源头已经改到 `~/Desktop/papers`。我还要把 server 和 `.env.example` 里的旧提示文字同步掉，不然实际行为和说明会打架。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `git diff -- .\paper_search_mcp\utils.py .\paper_search_mcp\server.py .\.env.example`
> - Ran `rg -n "~/Desktop" .\paper_search_mcp .\.env.example .\tests`
>
> </details>
>
> 我看到旧文案还散在 `server.py` 的 docstring 里。接下来我做一次机械替换，把对外说明也改成 `~/Desktop/papers`，实际默认值已经在 `utils.py` 改好了。
>
> <details><summary>Ran 1 command</summary>
>
> - Ran `$files = @('.\paper_search_mcp\server.py', '.\.env.example'); foreach ($file in $files) { $text = Get-Content -LiteralPath $file -Raw; $text = $text -replace '~/Desktop', '~/Desktop/papers'; Set-Content -LiteralPath $file -Value $text -NoNewline }`
>
> </details>
>
> 默认值已经改完，我刚才顺手把旧文案也替换了；现在我检查一下有没有留下 `~/Desktop` 的旧说明，并跑一遍相关测试确认策略 guard 仍然按新默认目录工作。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `rg -n "~/Desktop(?!/papers)|Desktop/papers" .\paper_search_mcp .\.env.example .\tests`
> - Ran `git diff -- .\paper_search_mcp\utils.py .\paper_search_mcp\server.py .\.env.example`
>
> </details>
>
> 我发现刚才那次机械替换顺手碰到了几处已有乱码片段的字节表现，虽然不是业务逻辑，但这属于无关改动。我会先把这些无关 diff 复原，只保留默认路径和相关说明的变化。
>
> <details><summary>Edited 1 file</summary>
>
> - Wrote `./paper_search_mcp/server.py` (+5 -5)
>
> </details>
>
> 我这边遇到一个小状况：刚才用 PowerShell 做批量文案替换时，`server.py` 里原本存在的乱码注释/文案被当前编码处理影响了，导致 `apply_patch` 读它时报 UTF-8 校验失败。这个我会先修正编码状态，只保留默认路径相关改动，不把无关乱码变化带进来。
>
> <details><summary>Ran 3 commands</summary>
>
> - Ran `$path='c:\code\paper-search-mcp\paper_search_mcp\server.py'; $bytes=[System.IO.File]::ReadAllBytes($path); try { $enc=New-Object System.Text.UTF8Encoding($false,$true); $null=$enc.GetString($bytes); 'valid utf8' } catch { $_.Exception.Message }; 'bytes=' + $bytes.Length`
> - Ran `$path='c:\code\paper-search-mcp\paper_search_mcp\server.py'; $bytes=[System.IO.File]::ReadAllBytes($path); $start=48380; $len=140; $slice=$bytes[$start..([Math]::Min($bytes.Length-1,$start+$len-1))]; $hex=($slice | ForEach-Object { $_.ToString('X2') }) -join ' '; $text=[System.Text.Encoding]::Default.GetString($slice); $hex; $text`
> - Ran `git diff --numstat -- .\paper_search_mcp\server.py .\paper_search_mcp\utils.py .\.env.example`
>
> </details>
>
> 我会先把 `server.py` 的编码修回有效 UTF-8，再继续验证默认路径。这个修复是针对我刚才批量替换造成的编码副作用，目标是只保留 `~/Desktop/papers` 相关变化。
>
> <details><summary>Ran 3 commands</summary>
>
> - Ran `$bytes=[System.IO.File]::ReadAllBytes('c:\code\paper-search-mcp\paper_search_mcp\server.py'); $bad=@(); for($i=0;$i -lt $bytes.Length-2;$i++){ if($bytes[$i] -eq 0xE9 -and $bytes[$i+1] -eq 0x88 -and $bytes[$i+2] -eq 0x3F){ $bad += $i } }; $bad -join ', '; 'count=' + $bad.Count`
> - Ran `git diff -- .\paper_search_mcp\server.py | Select-Object -First 220`
> - Ran `git show HEAD:paper_search_mcp/server.py | Select-String -Pattern "Sort criterion|Sort order|Optional IEEE|Optional ACM"`
>
> </details>
>
> 我继续把这个编码副作用收拾干净。现在的目标很窄：修回 `server.py` 为有效 UTF-8，然后确认默认路径源头和对外文案都是 `~/Desktop/papers`。
>
> <details><summary>Ran 3 commands</summary>
>
> - Ran `git show HEAD:paper_search_mcp/server.py | Select-String -Pattern "Sort criterion|Sort direction|Sort order|Optional IEEE|Optional ACM"`
> - Ran `$path='c:\code\paper-search-mcp\paper_search_mcp\server.py'; $bytes=[System.IO.File]::ReadAllBytes($path); $bad=@(); for($i=0;$i -lt $bytes.Length-2;$i++){ if($bytes[$i] -eq 0xE9 -and $bytes[$i+1] -eq 0x88 -and $bytes[$i+2] -eq 0x3F){ $bad += $i } }; "bad=$($bad -join ',') count=$($bad.Count)"`
> - Ran `$path='c:\code\paper-search-mcp\paper_search_mcp\server.py'; $bytes=[System.IO.File]::ReadAllBytes($path); foreach($idx in @(48431,48545,49160,100853,103055)){ $s=[Math]::Max(0,$idx-50); $e=[Math]::Min($bytes.Length-1,$idx+80); $slice=$bytes[$s..$e]; "--- $idx ---"; [System.Text.Encoding]::Default.GetString($slice) }`
>
> </details>
>
> 我定位到了 5 处坏字节：都是原本的 `鈥?` 这类既有乱码片段在批量替换时丢了一个 UTF-8 字节。修复方式很小：把缺失的字节补回去，让文件重新成为有效 UTF-8，再继续跑测试。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `$path='c:\code\paper-search-mcp\paper_search_mcp\server.py'; $bytes=[System.Collections.Generic.List[byte]]::new(); [byte[]]$raw=[System.IO.File]::ReadAllBytes($path); $i=0; while($i -lt $raw.Length){ if($i -le $raw.Length-3 -and $raw[$i] -eq 0xE9 -and $raw[$i+1] -eq 0x88 -and $raw[$i+2] -eq 0x3F){ $bytes.Add(0xE9); $bytes.Add(0x88); $bytes.Add(0xA5); $bytes.Add(0x3F); $i += 3 } else { $bytes.Add($raw[$i]); $i++ } }; [System.IO.File]::WriteAllBytes($path, $bytes.ToArray()); $enc=New-Object System.Text.UTF8Encoding($false,$true); $null=$enc.GetString([System.IO.File]::ReadAllBytes($path)); 'server.py valid utf8'`
> - Ran `rg -n "~/Desktop" .\paper_search_mcp .\.env.example .\tests`
>
> </details>
>
> 编码副作用已经修回来了，`server.py` 现在是有效 UTF-8。接下来我跑一遍针对测试和 diff 检查，确认新默认路径和上次的路径 guard 没互相打架。
>
> <details><summary>Ran 3 commands</summary>
>
> - Ran `uv run python -m unittest tests.test_selection_sessions tests.test_fallback tests.test_server.TestPaperSearchServer.test_download_arxiv_rejects_custom_save_path_by_default tests.test_server.TestPaperSearchServer.test_parse_pdf_with_mineru_pypdf_mode`
> - Ran `git diff --check`
> - Ran `uv run python - <<'PY'
from paper_search_mcp.utils import DEFAULT_SAVE_PATH, resolve_save_path
print(DEFAULT_SAVE_PATH)
print(resolve_save_path())
PY`
>
> </details>
>
> 测试已经通过了；刚才最后那个默认路径打印命令用了 bash 风格 heredoc，PowerShell 不吃这个语法。我换成 PowerShell 兼容的一行命令确认一下解析出来的绝对路径。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `uv run python -c "from paper_search_mcp.utils import DEFAULT_SAVE_PATH, resolve_save_path; print(DEFAULT_SAVE_PATH); print(resolve_save_path())"`
> - Ran `git diff -- .\paper_search_mcp\utils.py .\paper_search_mcp\server.py .\.env.example | Select-String -Pattern "DEFAULT_SAVE_PATH|Desktop/papers|invalid_save_path|ALLOW_CUSTOM"`
>
> </details>
>
> 我已经把代码默认值改完并验证了；我再补一眼 README 里是否还写着旧默认路径。因为 README 当前已有未归属修改，我只会做最小范围的旧路径文案替换，不碰其它内容。
>
> <details><summary>Ran 2 commands</summary>
>
> - Ran `rg -n "~/Desktop|Desktop\\example|Desktop/papers|save_path.*Desktop|default.*Desktop|Default.*Desktop" .\README.md .\README_CN.md .\claude-code\SKILL.md`
> - Ran `git diff -- .\README.md .\README_CN.md | Select-Object -First 160`
>
> </details>
>
> 我已经确认代码层默认值生效为 `~/Desktop/papers`，对应绝对路径在当前机器解析成 `C:\Users\...\Desktop\papers`。我再做最后一轮小整理：把辅助 skill 文档里的旧 `~/Desktop` 默认示例同步为 `~/Desktop/papers`，README 因为已有较多未归属改动我会避开。
>
> <details><summary>Ran 3 commands</summary>
>
> - Ran `Get-Content -LiteralPath .\tests\test_config_env.py`
> - Ran `git status --short`
> - Ran `Get-Content -LiteralPath .\claude-code\SKILL.md | Select-Object -First 140`
>
> </details>
>
> 我会顺手把辅助 skill 文档里的默认示例也同步到 `~/Desktop/papers`，这样以后我自己调用这个工具时也不会再被旧示例带偏。README 里已有大量未归属格式改动，我这次不混进去。
>
> <details><summary>Edited 1 file</summary>
>
> - Wrote `./claude-code/SKILL.md` (+5 -5)
>
> </details>
>
> 我最后再做一次快速检查：确认代码和辅助 skill 文档里已经没有旧的 `~/Desktop` 默认示例，并看一下当前改动列表，方便你判断哪些是这次相关的。
>
> <details><summary>Ran 3 commands</summary>
>
> - Ran `rg -n "~/Desktop(?!/papers)" --pcre2 .\paper_search_mcp .\.env.example .\claude-code\SKILL.md`
> - Ran `git status --short`
> - Ran `git diff --check`
>
> </details>

</details>

已将默认保存路径改为 `~/Desktop/papers`。

主要改动：

- [utils.py](./paper_search_mcp/utils.py:5)：`DEFAULT_SAVE_PATH = "~/Desktop/papers"`
- [server.py](./paper_search_mcp/server.py:88)：MCP 相关说明和路径约束文案同步为 `~/Desktop/papers`
- [.env.example](./.env.example:15)：默认下载目录说明同步
- [SKILL.md](./claude-code/SKILL.md:31)：辅助 skill 示例同步

验证结果：

```powershell
uv run python -c "from paper_search_mcp.utils import DEFAULT_SAVE_PATH, resolve_save_path; print(DEFAULT_SAVE_PATH); print(resolve_save_path())"
```

输出确认默认值为：

```text
~/Desktop/papers
C:\Users\마웸\Desktop\papers
```

相关测试也已通过：

```powershell
uv run python -m unittest tests.test_selection_sessions tests.test_fallback tests.test_server.TestPaperSearchServer.test_download_arxiv_rejects_custom_save_path_by_default tests.test_server.TestPaperSearchServer.test_parse_pdf_with_mineru_pypdf_mode
```

另外，`README.md` / `README_CN.md` 目前已有未归属修改，我没有混入这次变更。

> 如何利用 MCP APPs 实现对 ctx.elicit 的 UI 渲染，真正在对话中显示 checkbox，请你给我完整的实现方案

<details><summary>4 previous messages</summary>

> 我先核对一下当前 MCP 官方关于 `elicitation` 和 MCP Apps/UI 的能力边界，再结合你这个 `paper-search-mcp` 的 FastMCP 实现给出一套能落地的方案。这个点很容易被“服务端已经调了 `ctx.elicit`”和“客户端真的能渲染 checkbox”混在一起。
>
> <details><summary>Searched web 7 times</summary>
>
> <details><summary>Searched web 7 times</summary>
>
> - Searched web for `MCP elicitation ctx.elicit checkbox UI official docs Model Context Protocol`
> - Searched web for `https://modelcontextprotocol.io/specification/draft/client/elicitation`
> - Searched web for `https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/`
> - Searched web for `https://apps.extensions.modelcontextprotocol.io/`
> - Searched web for `https://apps.extensions.modelcontextprotocol.io/`
> - Searched web for `https://raw.githubusercontent.com/modelcontextprotocol/ext-apps/main/specification/2026-01-26/apps.mdx`
> - Searched web for `https://raw.githubusercontent.com/modelcontextprotocol/ext-apps/main/specification/2026-01-26/apps.mdx`
>
> </details>
>
> </details>
>
> 我查到关键边界了：`ctx.elicit` 和 MCP Apps 是两条不同机制。`ctx.elicit` 要求“客户端/Host”实现 `elicitation/create`；MCP Apps 则是工具返回 `ui://` HTML 资源，让 Host 在 iframe 中渲染交互界面。下面我会按这两个层次给你完整落地方案。
>
> <details><summary>Ran 6 commands, searched web 5 times</summary>
>
> - Ran `uv run python -c "import mcp, pkgutil; import mcp.server.fastmcp as f; print(mcp.__file__); print(f.__file__)"`
>
> - Ran `uv run python -c "import inspect; from mcp.server.fastmcp import FastMCP; print(FastMCP); print([m for m in dir(FastMCP) if 'resource' in m.lower() or 'tool' in m.lower()][:80]); print(inspect.signature(FastMCP.tool)); print(inspect.signature(FastMCP.resource))"`
>
> - Ran `rg -n "_build_paper_selection_schema|create_model|selected_papers|ctx.elicit|elicit" .\paper_search_mcp\server.py -C 4`
>
> <details><summary>Searched web 1 time</summary>
>
> - Searched web for `site:modelcontextprotocol.io elicitation create MCP specification`
>
> </details>
>
> - Ran `uv run python -c "import inspect; from mcp.server.fastmcp.server import FastMCP; import mcp.server.fastmcp.server as s; print(inspect.getsource(FastMCP.add_tool)[:2000])"`
>
> - Ran `uv run python -c "import inspect; import mcp.types as t; print(hasattr(t,'CallToolResult')); print(t.CallToolResult); print(inspect.signature(t.CallToolResult)); print(t.TextContent); print(inspect.signature(t.TextContent))"`
>
> <details><summary>Searched web 1 time</summary>
>
> - Searched web for `Model Context Protocol Apps extension UIResource outputTemplate docs`
>
> </details>
>
> - Ran `$i=0; Get-Content -LiteralPath .\paper_search_mcp\server.py | ForEach-Object { $i++; if($i -ge 438 -and $i -le 455){ '{0,4}: {1}' -f $i, $_ } }`
>
> <details><summary>Searched web 3 times</summary>
>
> - Searched web for `MCP Apps SDK outputTemplate ui:// resource widgetAccessible openai official`
> - Searched web for `https://developers.openai.com/apps-sdk/reference`
> - Searched web for `modelcontextprotocol elicitation create client capability docs`
>
> </details>
>
> </details>

</details>

可以，但要先澄清一个关键点：**MCP Apps 不能“渲染 `ctx.elicit` 本身”**。`ctx.elicit` 是 MCP Elicitation 协议，UI 是否出现由 **MCP Client/Host** 决定；协议只要求客户端声明并处理 `elicitation` capability，并不规定一定显示 checkbox。官方也明确说 elicitation 的界面模式由客户端实现决定。MCP Apps 则是另一条路：服务端返回一个 `ui://` HTML widget，让对话客户端在 iframe 里渲染你自己的 UI。这个方案能真正显示 checkbox。

推荐做法：**保留 `ctx.elicit` 作为原生客户端路径，同时新增 MCP Apps checkbox widget 作为强 UI 路径。**

**目标架构**

1. `search_papers_for_parsing`：继续作为数据工具，返回 `selection_token + papers`。
2. 新增 `render_paper_selection_app`：渲染 checkbox UI 的工具。
3. 新增 `ui://paper-search/paper-selection.html` 资源：真正的 checkbox 页面。
4. checkbox 页面通过 Apps bridge 调用 `parse_selected_papers`。
5. 客户端支持 Apps 时显示 UI；不支持时继续走 `selection_token + 编号选择` 降级。

官方依据：Apps 工具通过 `_meta.ui.resourceUri` / `_meta["openai/outputTemplate"]` 关联 UI 资源，widget 通过 `tools/call` 调 MCP 工具；ChatGPT 会加载 `text/html;profile=mcp-app` 资源并把 tool result 传给 iframe。参考 OpenAI Apps SDK 文档和 MCP Apps 说明。
Sources: [Apps SDK Reference](https://developers.openai.com/apps-sdk/reference), [Build MCP server](https://developers.openai.com/apps-sdk/build/mcp-server), [Build ChatGPT UI](https://developers.openai.com/apps-sdk/build/chatgpt-ui), [MCP Apps blog](https://blog.modelcontextprotocol.io/posts/2025-11-21-mcp-apps/)

**服务端实现草案**

在 [server.py](./paper_search_mcp/server.py) 增加：

```python
PAPER_SELECTION_WIDGET_URI = "ui://paper-search/paper-selection.html"

@mcp.resource(
    PAPER_SELECTION_WIDGET_URI,
    name="Paper Selection Widget",
    mime_type="text/html;profile=mcp-app",
)
async def paper_selection_widget() -> str:
    return PAPER_SELECTION_WIDGET_HTML

@mcp.tool(
    name="render_paper_selection_app",
    meta={
        "ui": {"resourceUri": PAPER_SELECTION_WIDGET_URI},
        "openai/outputTemplate": PAPER_SELECTION_WIDGET_URI,
        "openai/widgetAccessible": True,
        "openai/toolInvocation/invoking": "Opening paper selector...",
        "openai/toolInvocation/invoked": "Paper selector ready.",
    },
    structured_output=True,
)
async def render_paper_selection_app(
    selection_token: str,
    papers: List[Dict[str, Any]],
    mode: str = "auto",
    backend: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    return {
        "selection_token": selection_token,
        "papers": papers,
        "mode": mode,
        "backend": backend,
        "force": force,
        "instructions": "Select papers and click Parse.",
    }
```

Widget HTML 核心：

```html
<div id="app"></div>
<script>
  const app = document.getElementById("app");

  function getOutput() {
    return window.openai?.toolOutput || {};
  }

  function render() {
    const data = getOutput();
    const papers = data.papers || [];

    app.innerHTML = `
    <style>
      body { font-family: system-ui; padding: 12px; }
      .paper { display: flex; gap: 8px; padding: 8px 0; border-bottom: 1px solid #ddd; }
      button { margin-top: 12px; padding: 8px 12px; }
      .status { margin-top: 10px; white-space: pre-wrap; }
    </style>
    <form id="form">
      ${papers
        .map(
          (p) => `
        <label class="paper">
          <input type="checkbox" value="${p.index}">
          <span><b>${p.index}. ${escapeHtml(
            p.title || "Untitled"
          )}</b><br>${escapeHtml(p.source || "")}</span>
        </label>
      `
        )
        .join("")}
      <button type="submit">Parse selected papers</button>
      <div class="status" id="status"></div>
    </form>
  `;

    document.getElementById("form").onsubmit = async (e) => {
      e.preventDefault();
      const selected = [...document.querySelectorAll("input:checked")].map(
        (x) => x.value
      );
      const status = document.getElementById("status");

      if (!selected.length) {
        status.textContent = "Please select at least one paper.";
        return;
      }

      status.textContent = "Parsing...";
      const result = await window.openai.callTool("parse_selected_papers", {
        selection_token: data.selection_token,
        selected_indices: selected.join(","),
        mode: data.mode || "auto",
        backend: data.backend || "",
        force: !!data.force,
      });

      status.textContent = JSON.stringify(
        result.structuredContent || result,
        null,
        2
      );
    };
  }

  function escapeHtml(s) {
    return String(s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }[c])
    );
  }

  render();
  window.addEventListener("openai:set_globals", render);
</script>
```

**调用流程**

支持 Apps 的客户端中，让模型按这个顺序调用：

1. `search_papers_for_parsing(query="agent skill", sources="arxiv,semantic")`
2. `render_paper_selection_app(selection_token=..., papers=...)`
3. 用户在 widget 中勾选论文。
4. widget 调 `parse_selected_papers(...)`。

**和 `ctx.elicit` 的关系**

你现在的 [server.py](./paper_search_mcp/server.py:1140) 已经调用 `ctx.elicit(...)`；schema 也已经是 `array + enum`，理论上客户端可以渲染成 checkbox。要让 `ctx.elicit` 原生显示 checkbox，必须修改/更换 MCP Host：

- 初始化时声明 `capabilities: { elicitation: {} }`
- 监听 `elicitation/create`
- 根据 `requestedSchema.properties.selected_papers.items.enum` 渲染 checkbox
- 用户提交后返回 `action: "accept"` 和选中的数组

如果你不控制 Host，比如当前 Codex 工具桥接不支持 elicitation UI，那服务端没法强制它显示 checkbox。**MCP Apps widget 是你可控的解决方案。**

> 请你按照以上方案执行
