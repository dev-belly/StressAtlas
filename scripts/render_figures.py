"""Render monetary comparison and additive ES contributions from saved outputs."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT=Path(__file__).resolve().parents[1]
plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":11, "text.color":"#edf2fa", "axes.labelcolor":"#afbdd2", "xtick.color":"#afbdd2", "ytick.color":"#afbdd2", "axes.edgecolor":"#334565", "axes.facecolor":"#131f35", "figure.facecolor":"#0d1424"})


def main():
    summary=json.loads((ROOT/"demo/summary.json").read_text())
    rows=summary["scenarios"]
    names=[r["scenario"].replace("_","\n") for r in rows]
    x=np.arange(len(rows))
    fig,(metrics,attribution)=plt.subplots(1,2,figsize=(14,6),layout="constrained")
    fig.suptitle("StressAtlas  |  Marginal risk and clustered defaults",fontsize=22,fontweight="bold",x=.035,ha="left")
    for offset,key,label,color in ((-.24,"analytic_el","Expected loss","#77c7df"),(0,"var","99% VaR","#a8a2ee"),(.24,"es","99% ES","#f4b76a")):
        metrics.bar(x+offset,[r[key]/1e6 for r in rows],width=.23,label=label,color=color)
    metrics.set_xticks(x,names)
    metrics.set_ylabel("Loss / CNY million")
    metrics.set_title("Same marginal risk ≠ same tail",loc="left",pad=14,fontsize=13)
    metrics.legend(frameon=False,fontsize=10,labelcolor="#e0e9f8",loc="upper left")
    sectors=sorted({r["sector"] for r in summary["sector_es"]})
    bottom=np.zeros(len(rows))
    for sector,color in zip(sectors,("#77c7df","#f4b76a","#8ea2dd","#77d7bc")):
        values=np.array([next(r["es_contribution"] for r in summary["sector_es"] if r["scenario"]==row["scenario"] and r["sector"]==sector)/1e6 for row in rows])
        attribution.bar(x,values,bottom=bottom,width=.65,label=sector.replace("_"," "),color=color)
        bottom+=values
    attribution.set_xticks(x,names)
    attribution.set_ylabel("Contribution to portfolio ES / CNY million")
    attribution.set_title("Sector contributions sum to portfolio ES",loc="left",pad=14,fontsize=13)
    attribution.legend(frameon=False,fontsize=9,labelcolor="#e0e9f8",loc="upper left")
    for ax in (metrics,attribution):
        ax.set_ylim(0,max(r["es"] for r in rows)/1e6*1.25)
        ax.spines[["top","right"]].set_visible(False)
        ax.grid(axis="y",color="#536981",alpha=.18)
        ax.set_axisbelow(True)
    fig.supxlabel("Synthetic assumptions · 160 loans / 80 obligors · 20,000 common paths · Source: demo/summary.json",fontsize=10,color="#8fa3bf")
    fig.savefig(ROOT/"docs/evidence.png",dpi=150)
    plt.close(fig)


if __name__=="__main__":
    main()
