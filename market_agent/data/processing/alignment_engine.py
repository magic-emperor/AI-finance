import pandas as pd
import structlog
from typing import List, Optional

logger = structlog.get_logger()

class MicroAlignmentEngine:
    """
    Layer 1: Resilient Alignment
    Aligns multiple high-frequency data streams without lookahead bias.
    Optimized for 16GB RAM using chunked results.
    """
    
    @staticmethod
    def align_streams(base_df: pd.DataFrame, overlay_dfs: List[pd.DataFrame], suffixes: List[str]) -> pd.DataFrame:
        """
        Main alignment logic using 'merge_asof'.
        base_df: The primary timeline (typically 1m price data).
        overlay_dfs: Other streams (Flow, Depth, Macro).
        """
        if base_df.empty:
            return base_df
            
        # Ensure base is sorted for asof
        aligned = base_df.sort_index()
        
        for i, df in enumerate(overlay_dfs):
            if df.empty:
                continue
                
            sorted_ovr = df.sort_index()
            # merge_asof aligns 'aligned' with the most recent entry in 'df' 
            # without going into the future (direction='backward')
            aligned = pd.merge_asof(
                aligned, 
                sorted_ovr, 
                left_index=True, 
                right_index=True, 
                direction='backward',
                suffixes=('', f'_{suffixes[i]}')
            )
            
        logger.info("alignment_complete", 
                    base_rows=len(base_df), 
                    final_columns=len(aligned.columns))
        return aligned

    @staticmethod
    def optimize_memory(df: pd.DataFrame) -> pd.DataFrame:
        """
        Downsamples to float32/fp16 to save RAM on 16GB machines.
        """
        for col in df.select_dtypes(include=['float64']).columns:
            df[col] = df[col].astype('float32')
        return df

if __name__ == "__main__":
    # Test Alignment
    t1 = pd.date_range("2023-01-01 09:15:00", periods=5, freq="1min")
    price_df = pd.DataFrame({"price": [100, 101, 102, 103, 104]}, index=t1)
    
    t2 = pd.date_range("2023-01-01 09:10:00", periods=2, freq="10min")
    flow_df = pd.DataFrame({"fii_flow": [500, 600]}, index=t2)
    
    engine = MicroAlignmentEngine()
    result = engine.align_streams(price_df, [flow_df], ["inst"])
    print("Alignment Result (Price + Lagged Flow):")
    print(result)
