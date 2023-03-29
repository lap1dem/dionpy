from __future__ import annotations

import itertools
import os
import shutil
import tempfile
from datetime import datetime
from multiprocessing import cpu_count, Pool
from typing import Tuple, Callable, Union, List, Sequence

import h5py
import numpy as np
from tqdm import tqdm

from .DLayer import DLayer
from .FLayer import FLayer
from .modules.helpers import none_or_array, elaz_mesh, TextColor, pic2vid
from .modules.ion_tools import trop_refr
from .modules.parallel import calc_refatt_par, calc_refatt
from .modules.plotting import polar_plot_star, polar_plot


class IonFrame:
    """
    A model of the ionosphere for a specific moment in time. Given a position, calculates electron
    density and temperature in the ionosphere in all visible directions using International Reference
    Ionosphere (IRI) model. The calculated model can estimate ionospheric attenuation and refraction
    in a given direction defined by elevation and azimuth angles.

    :param dt: Date/time of the model.
    :param position: Geographical position of an observer. Must be a tuple containing
                     latitude [deg], longitude [deg], and elevation [m].
    :param nside: Resolution of healpix grid.
    :param dbot: Lower limit in [km] of the D layer of the ionosphere.
    :param dtop: Upper limit in [km] of the D layer of the ionosphere.
    :param ndlayers: Number of sub-layers in the D layer for intermediate calculations.
    :param fbot: Lower limit in [km] of the F layer of the ionosphere.
    :param ftop: Upper limit in [km] of the F layer of the ionosphere.
    :param nflayers: Number of sub-layers in the F layer for intermediate calculations.
    :param iriversion: Version of the IRI model to use. Must be a two digit integer that refers to
                        the last two digits of the IRI version number. For example, version 20 refers
                        to IRI-2020.
    :param autocalc: If True - the model will be calculated immediately after definition.
    :param _pbar: If True - a progress bar will appear.
    """

    def __init__(
        self,
        dt: datetime,
        position: Sequence[float, float, float],
        nside: int = 64,
        dbot: float = 60,
        dtop: float = 90,
        ndlayers: int = 100,
        fbot: float = 150,
        ftop: float = 500,
        nflayers: int = 100,
        iriversion: int = 20,
        autocalc: bool = True,
        _pbar: bool = False,
        _pool: Union[Pool, None] = None,
        _apf107_args: List | None = None
    ):
        if isinstance(dt, datetime):
            self.dt = dt
        else:
            raise ValueError("Parameter dt must be a datetime object.")
        self.position = position
        self.nside = nside
        self.iriversion = iriversion
        self.dlayer = DLayer(
            dt, position, dbot, dtop, ndlayers, nside, iriversion, autocalc, _pbar, _pool, _apf107_args,
        )
        self.flayer = FLayer(
            dt, position, fbot, ftop, nflayers, nside, iriversion, autocalc, _pbar, _pool, _apf107_args,
        )

    @staticmethod
    def _parallel_calc(func, el, az, freq, pbar_desc, **kwargs):
        """
        Sends methods either to serial or parallel calculation routines based on type of freq.
        """
        if (isinstance(freq, list) or isinstance(freq, np.ndarray)) and len(freq) > 1:
            return calc_refatt_par(func, el, az, freq, pbar_desc, **kwargs)
        else:
            return calc_refatt(func, el, az, freq, **kwargs)

    @staticmethod
    def troprefr(el: float | np.ndarray) -> float | np.ndarray:
        """
        Approximation of the refraction in the troposphere recommended by the ITU-R:
        https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.834-9-201712-I!!PDF-E.pdf

        :param el: Elevation of observation(s) in [deg].
        :return: Refraction in the troposphere in [deg].
        """
        return np.rad2deg(trop_refr(np.deg2rad(90 - el)))

    def refr(
        self,
        el: float | np.ndarray,
        az: float | np.ndarray,
        freq: float | np.ndarray,
        troposphere: bool = True,
        _pbar_desc: str | None = None,
    ):
        """
        :param el: Elevation of observation(s) in [deg].
        :param az: Azimuth of observation(s) in [deg].
        :param freq: Frequency of observation(s) in [MHz]. If array - the calculation will be performed in parallel on
                     all available cores. Requires `dt` to be a single datetime object.
        :param troposphere: If True - the troposphere refraction correction will be applied before calculation.
        :param _pbar_desc: Description of progress bar. If None - the progress bar will not appear.
        :return: Refraction angle in [deg] at given sky coordinates, time and frequency of observation.
        """
        return self._parallel_calc(
            self.flayer.refr, el, az, freq, _pbar_desc, troposphere=troposphere
        )

    def atten(
        self,
        el: float | np.ndarray,
        az: float | np.ndarray,
        freq: float | np.ndarray,
        _pbar_desc: str | None = None,
        col_freq: str = "default",
        troposphere: bool = True,
    ) -> float | np.ndarray:
        """
        :param el: Elevation of observation(s) in [deg].
        :param az: Azimuth of observation(s) in [deg].
        :param freq: Frequency of observation(s) in [MHz]. If  - the calculation will be performed in parallel on all
                     available cores. Requires `dt` to be a single datetime object.
        :param col_freq: Collision frequency model. Available options: 'default', 'nicolet', 'setty', 'aggrawal',
                         or float in Hz.
        :param troposphere: If True - the troposphere refraction correction will be applied before calculation.
        :param _pbar_desc: Description of progress bar. If None - the progress bar will not appear.
        :return: Attenuation factor at given sky coordinates, time and frequency of observation. Output is the
                 attenuation factor between 0 (total attenuation) and 1 (no attenuation).
        """
        return self._parallel_calc(
            self.dlayer.atten,
            el,
            az,
            freq,
            _pbar_desc,
            col_freq=col_freq,
            troposphere=troposphere,
        )

    def write_self_to_file(self, file: h5py.File):
        h5dir = f"{self.dt.year:04d}{self.dt.month:02d}{self.dt.day:02d}{self.dt.hour:02d}{self.dt.minute:02d}"
        grp = file.create_group(h5dir)
        meta = grp.create_dataset("meta", shape=(0,))
        meta.attrs["position"] = self.position
        meta.attrs["dt"] = self.dt.strftime("%Y-%m-%d %H:%M")
        meta.attrs["nside"] = self.nside
        meta.attrs["iriversion"] = self.iriversion

        meta.attrs["ndlayers"] = self.dlayer.nlayers
        meta.attrs["dtop"] = self.dlayer.htop
        meta.attrs["dbot"] = self.dlayer.hbot

        meta.attrs["nflayers"] = self.flayer.nlayers
        meta.attrs["fbot"] = self.flayer.hbot
        meta.attrs["ftop"] = self.flayer.htop

        if np.average(self.dlayer.edens) > 0 and np.average(self.flayer.edens) > 0:
            grp.create_dataset("dedens", data=self.dlayer.edens)
            grp.create_dataset("detemp", data=self.dlayer.etemp)
            grp.create_dataset("fedens", data=self.flayer.edens)
            grp.create_dataset("fetemp", data=self.flayer.etemp)

    def save(self, saveto: str = "./ionframe"):
        """
        Save the model to HDF file.

        :param saveto: Path and name of the file.
        """
        head, tail = os.path.split(saveto)
        if not os.path.exists(head) and len(head) > 0:
            os.makedirs(head)
        if not saveto.endswith(".h5"):
            saveto += ".h5"

        file = h5py.File(saveto, mode="w")
        self.write_self_to_file(file)
        file.close()

    @classmethod
    def read_self_from_file(cls, grp: h5py.Group):
        meta = grp.get("meta")
        obj = cls(
            autocalc=False,
            dt=datetime.strptime(meta.attrs["dt"], "%Y-%m-%d %H:%M"),
            position=meta.attrs["position"],
            nside=meta.attrs["nside"],
            dbot=meta.attrs["dbot"],
            dtop=meta.attrs["dtop"],
            ndlayers=meta.attrs["ndlayers"],
            fbot=meta.attrs["fbot"],
            ftop=meta.attrs["ftop"],
            nflayers=meta.attrs["nflayers"],
            iriversion=meta.attrs["iriversion"],
        )
        obj.dlayer.edens = none_or_array(grp.get("dedens"))
        obj.dlayer.etemp = none_or_array(grp.get("detemp"))

        obj.flayer.edens = none_or_array(grp.get("fedens"))
        obj.flayer.etemp = none_or_array(grp.get("fetemp"))
        return obj

    @classmethod
    def load(cls, path: str):
        """
        Load a model from file.

        :param path: Path to a file (file extension is not required).
        :return: :class:`IonModel` recovered from a file.
        """
        if not path.endswith(".h5"):
            path += ".h5"
        with h5py.File(path, mode="r") as file:
            groups = list(file.keys())
            if len(groups) > 1:
                raise RuntimeError(
                    "File contains more than one model. "
                    + "Consider reading it with IonModel class."
                )

            grp = file[groups[0]]
            obj = cls.read_self_from_file(grp)
        return obj

    def plot_ded(self, gridsize: int = 200, layer: int | None = None, **kwargs):
        """
        Visualize electron density in the D layer.

        :param gridsize: Grid resolution of the plot.
        :param layer: A specfic layer to plot. If None - an average of all layers is calculated.
        :param kwargs: See `dionpy.plot_kwargs`.
        :return: A matplotlib figure.
        """
        barlabel = r"$m^{-3}$"
        el, az = elaz_mesh(gridsize)
        ded = self.dlayer.ed(el, az, layer)
        return polar_plot(
            (np.deg2rad(az), 90 - el, ded),
            dt=self.dt,
            pos=self.position,
            barlabel=barlabel,
            **kwargs,
        )

    def plot_det(self, gridsize: int = 200, layer: int | None = None, **kwargs):
        """
        Visualize electron temperature in the D layer.

        :param gridsize: Grid resolution of the plot.
        :param layer: A specfic layer to plot. If None - an average of all layers is calculated.
        :param kwargs: See `dionpy.plot_kwargs`.
        :return: A matplotlib figure.
        """
        barlabel = r"$K^\circ$"
        el, az = elaz_mesh(gridsize)
        det = self.dlayer.et(el, az, layer)
        return polar_plot(
            (np.deg2rad(az), 90 - el, det),
            dt=self.dt,
            pos=self.position,
            barlabel=barlabel,
            **kwargs,
        )

    def plot_fed(self, gridsize: int = 200, layer: int | None = None, **kwargs):
        """
        Visualize electron density in the F layer.

        :param gridsize: Grid resolution of the plot.
        :param layer: A specfic layer to plot. If None - an average of all layers is calculated.
        :param kwargs: See `dionpy.plot_kwargs`.
        :return: A matplotlib figure.
        """
        barlabel = r"$m^{-3}$"
        el, az = elaz_mesh(gridsize)
        fed = self.flayer.ed(el, az, layer)
        return polar_plot(
            (np.deg2rad(az), 90 - el, fed),
            dt=self.dt,
            pos=self.position,
            barlabel=barlabel,
            **kwargs,
        )

    def plot_fet(self, gridsize: int = 200, layer: int | None = None, **kwargs):
        """
        Visualize electron temperature in the F layer.

        :param gridsize: Grid resolution of the plot.
        :param layer: A specfic layer to plot. If None - an average of all layers is calculated.
        :param kwargs: See `dionpy.plot_kwargs`.
        :return: A matplotlib figure.
        """
        barlabel = r"$K^\circ$"
        el, az = elaz_mesh(gridsize)
        fet = self.flayer.et(el, az, layer)
        return polar_plot(
            (np.deg2rad(az), 90 - el, fet),
            dt=self.dt,
            pos=self.position,
            barlabel=barlabel,
            **kwargs,
        )

    def plot_atten(
        self, freq: float, troposphere: bool = True, gridsize: int = 200, **kwargs
    ):
        """
        Visualize ionospheric attenuation.

        :param freq: Frequency of observation in [Hz].
        :param troposphere: If True - the troposphere refraction correction will be applied before calculation.
        :param gridsize: Grid resolution of the plot.
        :param kwargs: See `dionpy.plot_kwargs`.
        :return: A matplotlib figure.
        """
        el, az = elaz_mesh(gridsize)
        atten = self.dlayer.atten(el, az, freq, troposphere=troposphere)
        # atten_db = 20 * np.log10(atten)
        # barlabel = r"dB"
        return polar_plot(
            (np.deg2rad(az), 90 - el, atten),
            dt=self.dt,
            pos=self.position,
            freq=freq,
            **kwargs,
        )

    def plot_refr(
        self,
        freq: float,
        troposphere: bool = True,
        gridsize: int = 200,
        cmap: str = "viridis_r",
        **kwargs,
    ):
        """
        Visualize ionospheric refraction.

        :param freq: Frequency of observation in [Hz].
        :param troposphere: If True - the troposphere refraction correction will be applied before calculation.
        :param gridsize: Grid resolution of the plot.
        :param cmap: A colormap to use in the plot.
        :param kwargs: See `dionpy.plot_kwargs`.
        :return: A matplotlib figure.
        """
        el, az = elaz_mesh(gridsize)
        refr = self.flayer.refr(el, az, freq, troposphere=troposphere)
        barlabel = r"$deg$"
        return polar_plot(
            (np.deg2rad(az), 90 - el, refr),
            dt=self.dt,
            pos=self.position,
            freq=freq,
            barlabel=barlabel,
            cmap=cmap,
            **kwargs,
        )

    def plot_troprefr(self, gridsize=200, cmap="viridis_r", **kwargs):
        """
        Visualize tropospheric refraction.

        :param gridsize: Grid resolution of the plot.
        :param cmap: A colormap to use in the plot.
        :param kwargs: See `dionpy.plot_kwargs`.
        :return: A matplotlib figure.
        """
        el, az = elaz_mesh(gridsize)
        troprefr = self.troprefr(el)
        barlabel = r"$deg$"
        return polar_plot(
            (np.deg2rad(az), 90 - el, troprefr),
            dt=self.dt,
            pos=self.position,
            barlabel=barlabel,
            cmap=cmap,
            **kwargs,
        )

    def _freq_animation(
        self,
        func: Callable,
        saveto: str,
        freqrange: Tuple[float, float] = (45, 125),
        gridsize: int = 200,
        fps: int = 20,
        duration: int = 5,
        title: str | None = None,
        barlabel: str | None = None,
        plotlabel: str | None = None,
        dpi: int = 300,
        cmap: str = "viridis",
        cbformat: str = "%.2f",
        pbar_label: str = "",
    ):
        print(
            TextColor.BOLD
            + TextColor.YELLOW
            + "Animation making procedure started"
            + f" [{pbar_label}]"
            + TextColor.END
            + TextColor.END
        )
        el, az = elaz_mesh(gridsize)
        nframes = duration * fps
        freqs = np.linspace(*freqrange, nframes)[::-1]
        data = np.array(func(el, az, freqs, _pbar_desc="[1/3] Calculating data"))
        cbmin, cbmax = np.nanmin(data[data != -np.inf]), np.nanmax(data[data != np.inf])

        tmpdir = tempfile.mkdtemp()
        nproc = np.min([cpu_count(), len(freqs)])
        plot_data = [(np.deg2rad(az), 90 - el, data[i]) for i in range(len(data))]
        plot_saveto = [os.path.join(tmpdir, str(i).zfill(6)) for i in range(len(data))]
        try:
            with Pool(processes=nproc) as pool:
                list(
                    tqdm(
                        pool.imap(
                            polar_plot_star,
                            zip(
                                plot_data,
                                itertools.repeat(self.dt),
                                itertools.repeat(self.position),
                                freqs,
                                itertools.repeat(title),
                                itertools.repeat(barlabel),
                                itertools.repeat(plotlabel),
                                itertools.repeat((cbmin, cbmax)),
                                plot_saveto,
                                itertools.repeat(dpi),
                                itertools.repeat(cmap),
                                itertools.repeat(cbformat),
                            ),
                        ),
                        desc="[2/3] Rendering frames",
                        total=len(freqs),
                    )
                )

            desc = "[3/3] Rendering video"
            pic2vid(tmpdir, saveto, fps=fps, desc=desc)
        except Exception as e:
            shutil.rmtree(tmpdir)
            print(e)
        else:
            shutil.rmtree(tmpdir)
        return

    def animate_atten_vs_freq(
        self,
        saveto: str = "./atten_vs_freq",
        freqrange: Tuple[float, float] = (45, 125),
        fps: int = 20,
        duration: int = 5,
        **kwargs,
    ):
        """
        Generates an animation of attenuation change with frequency.

        :param saveto: Path and name of file.
        :param freqrange: Frequency range of animation.
        :param fps: Frames per second.
        :param duration: Duration of animation in [s].
        :param kwargs: See `dionpy.plot_kwargs`.
        """
        self._freq_animation(
            self.atten,
            saveto=saveto,
            freqrange=freqrange,
            fps=fps,
            duration=duration,
            pbar_label="D layer attenuation",
            cbformat="%.3f",
            **kwargs,
        )

    def animate_refr_vs_freq(
        self,
        saveto: str = "./refr_vs_freq",
        freqrange: Tuple[float, float] = (45, 125),
        fps: int = 20,
        duration: int = 5,
        cmap="viridis_r",
        **kwargs,
    ):
        """
        Generates an animation of refraction angle change with frequency.

        :param saveto: Path and name of file.
        :param freqrange: Frequency range of animation.
        :param fps: Frames per second.
        :param duration: Duration of animation in [s].
        :param cmap: Matplotlib colormap to use in plot.
        :param kwargs: See `dionpy.plot_kwargs`.
        """
        self._freq_animation(
            self.refr,
            saveto=saveto,
            freqrange=freqrange,
            fps=fps,
            duration=duration,
            pbar_label="F layer refraction",
            barlabel=r"deg",
            cmap=cmap,
            cbformat="%.2f",
            **kwargs,
        )
