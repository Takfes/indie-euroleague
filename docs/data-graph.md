<!-- GENERATED — do not edit. Regenerated from the Inputs/Outputs/Final header of every script in src/eupy/. -->

# Data graph

> **GENERATED — do not edit.** Lineage derived from script I/O headers. Rounded = external sources, rectangles = scripts, cylinders = raw datasets (`stage 0`), double-bordered = produced datasets with `final: true` (exposed under `data/stage_99/` as relative symlinks). Inventory: [data-catalogue.md](data-catalogue.md).

```mermaid
flowchart LR
    %% external sources
    bs(["basketballsphere.com"])
    dunkest(["fantaking-api.dunkest.com"])
    live(["live.euroleague.net"])
    adv(["euroleague-advanced-api.eu"])
    kaggle(["Kaggle (manual download)"])
    verdicts(["verdicts JSON (--verdicts)"])
    manual(["manual preparation"])

    %% scripts
    f_bs["fetch_basketballsphere_prices"]
    f_fs["fetch_euroleague_fantasy_stats"]
    f_live["fetch_euroleague_live_boxscores"]
    f_lhdr["fetch_euroleague_live_headers"]
    f_sched["fetch_euroleague_schedule"]
    b_turns["build_schedule_turns"]
    r_p["resolve_player_names"]
    a_p["apply_player_name_verdicts"]
    r_t["resolve_team_names"]
    a_t["apply_team_name_verdicts"]
    o_sq["optimize_squad"]
    t_app["append_live_boxscores"]
    t_hdr["append_live_headers"]

    %% raw datasets (stage 0)
    d_prices[("basketballsphere_prices<br/>stage 0")]
    d_players[("euroleague_fantasy_stats/players<br/>stage 0")]
    d_coaches[("euroleague_fantasy_stats/head_coaches<br/>stage 0")]
    d_live[("euroleague_live/box_score<br/>stage 0")]
    d_lhdr[("euroleague_live/headers<br/>stage 0")]
    d_sched[("euroleague_schedule/schedule<br/>stage 0")]
    d_kbox[("kaggle_data/euroleague_box_score<br/>stage 0")]
    d_khdr[("kaggle_data/euroleague_header<br/>stage 0")]
    d_opt[("optimizer_input<br/>stage 0")]

    %% produced datasets
    d_hdrc["header_current<br/>stage 1"]
    d_bsc["box_score_current<br/>stage 1"]
    d_pxw[["player_name_crosswalk<br/>stage 1 · final"]]
    d_txw[["team_name_crosswalk<br/>stage 1 · final"]]
    d_sq[["squad_solution<br/>stage 1 · final"]]
    d_sch[["schedule<br/>stage 1 · final"]]
    d_trt[["team_round_turn<br/>stage 1 · final"]]

    bs --> f_bs --> d_prices
    dunkest --> f_fs
    f_fs --> d_players
    f_fs --> d_coaches
    live --> f_live --> d_live
    live --> f_lhdr --> d_lhdr
    adv --> f_sched --> d_sched
    d_sched --> b_turns
    b_turns --> d_sch
    b_turns --> d_trt
    kaggle --> d_kbox
    kaggle --> d_khdr

    d_prices --> r_p
    d_kbox --> r_p
    r_p --> d_pxw
    d_pxw --> a_p --> d_pxw
    verdicts --> a_p

    d_prices --> r_t
    d_khdr --> r_t
    r_t --> d_txw
    d_txw --> a_t --> d_txw
    verdicts --> a_t

    d_kbox --> t_app
    d_live --> t_app
    t_app --> d_bsc

    d_khdr --> t_hdr
    d_lhdr --> t_hdr
    t_hdr --> d_hdrc

    manual --> d_opt
    d_opt --> o_sq --> d_sq
```
