import dataikuBirdPelletLoader from '../assets/dataiku-bird-pellet-loader.svg';

export function PacmanLoader() {
  return (
    <div className="loader-container dataiku-bird-loader" aria-hidden="true">
      <img
        src={dataikuBirdPelletLoader}
        alt=""
        className="dataiku-bird-loader__art"
        draggable={false}
      />
    </div>
  );
}
